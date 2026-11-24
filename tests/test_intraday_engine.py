from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text

from backend.app.db import DB, CheckRun, DailyBar, DailyBrief, Holding, IntradayReading, RiskTransition, Setting
from backend.app.intraday import record_transitions
from backend.app.scanner import ScanService
from backend.app.scheduler import plan_status, scan_minutes
from scripts import market_probe as probe
from stock_radar.rules.engine import evaluate_history
from test_scan import sample_rows


class IntradayEngineTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = DB("sqlite:///" + str(Path(tmp.name) / "app.db"))
        self.addCleanup(self.db.engine.dispose)
        self.db.migrate()
        self.env = patch.dict(os.environ, {"SCHEDULER_ENABLED":"false", "INTRADAY_VALIDATED":"true",
                               "SERVERCHAN_SENDKEY":"SCTfixture", "HITHINK_FINANCE_API_KEY":"fixture"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = datetime(2026, 11, 19, 15, 45, tzinfo=probe.SHANGHAI)
        self.price = 20.0
        self.overrides = {}
        self.raw_rows = sample_rows(self.now.date())
        # Flat history gives formal RSI=50 before the test session.
        for row in self.raw_rows:
            row.update(high=20.0, low=20.0, open=20.0, close=20.0)
        self.history = Mock(side_effect=lambda stocks,start,end: [
            (s,self.raw_rows,None) for s,m in stocks])
        self.quotes = Mock(side_effect=self.fetch)
        self.sender = Mock(return_value=("SENT", "0", None))
        self.service = ScanService(self.db, fetch=self.history, snapshot_fetch=self.quotes,
            clock=lambda:self.now, sender=self.sender)
        self.service.start()
        self.addCleanup(self.service.stop)
        with self.db.sessions.begin() as session:
            session.add(Holding(symbol="600519.SH", market="A"))
            prefs = session.get(Setting,1)
            prefs.intraday_enabled, prefs.notifications_enabled = True, True
        self.finish_manual()
        self.now = self.now.replace(day=20, hour=10, minute=0)

    def fetch(self, db, stocks, now, priority):
        return {s:{"price":self.price,"prev_close":20.,"quote_time":now.isoformat(),
                   "open":20.,"low":min(20.,self.price),"high":max(20.,self.price),
                   "provider":"hithink","name":"样例","currency":"CNY","adjustment_verified":True,
                   **self.overrides} for s,m in stocks}

    def finish_manual(self):
        rid = self.service.trigger()
        self.wait()
        return rid

    def wait(self):
        self.service.pool.submit(lambda:None).result(timeout=10)

    def test_calendar_slots_and_independent_brief(self):
        self.assertEqual(sorted(scan_minutes(30)),[600,630,660,690,780,810,840,870])
        for interval in (15,30,60):
            self.assertIn(690,scan_minutes(interval))
            self.assertIn(780,scan_minutes(interval))
        with self.db.sessions.begin() as session:
            prefs = session.get(Setting,1)
            prefs.interval_minutes=60
        p=plan_status(self.db,self.now)
        self.assertEqual(p["planned_today"],6)
        self.assertIn("14:30",p["next_brief_at"])
        self.assertEqual(plan_status(self.db,self.now.replace(day=21))["planned_today"],0)
        self.assertIsNone(plan_status(self.db,self.now.replace(year=2028))["next_scan_at"])

    def test_correct_daily_cross_lifecycle_and_close_confirmation(self):
        self.price=21.
        self.finish_manual()
        self.now += timedelta(minutes=30)
        self.finish_manual()
        self.price=19.
        self.now += timedelta(minutes=30)
        self.finish_manual()
        self.price=22.
        self.now += timedelta(minutes=30)
        self.finish_manual()
        with self.db.sessions() as s:
            readings = s.scalars(select(IntradayReading).order_by(IntradayReading.id)).all()
            self.assertEqual(len(readings),4)
            self.assertTrue(all(r.data_status=="OK" for r in readings))
            for r in readings:
                result = evaluate_history([20.]*105 + [r.price])
                self.assertEqual([r.rsi6,r.rsi12,r.rsi24],[result[f] for f in ("rsi6","rsi12","rsi24")])
            self.assertEqual(s.get(Holding,1).bar_date,"2026-11-19")
            self.assertEqual(s.scalar(select(func.count(DailyBar.id))),105)
            events=s.scalars(select(RiskTransition).order_by(RiskTransition.id)).all()
            self.assertEqual([e.stage for e in events],["PROVISIONAL","CLEARED","PROVISIONAL"])
            self.assertEqual(events[-1].parent_id,events[1].id)
            last = events[-1].id
        self.now=self.now.replace(hour=15,minute=45)
        self.raw_rows.append({"date":"2026-11-20","high":22.,"low":20.,"open":20.,"close":22.,"volume":1200})
        self.finish_manual()
        with self.db.sessions() as s:
            close=s.scalars(select(RiskTransition).where(RiskTransition.stage=="CONFIRMED")).one()
            self.assertEqual(close.parent_id,last)
            self.assertEqual(s.get(Holding,1).bar_date,"2026-11-20")
            self.assertEqual(s.scalar(select(func.count(DailyBar.id))),106)
        self.assertEqual(self.sender.call_count,2)

    def test_batch_time_without_row_trade_time_and_stale_batch(self):
        self.price = 21.
        self.overrides = {"quote_time": None, "batch_time": self.now.isoformat(),
                          "source_trade_date": self.now.date().isoformat(), "volume": 1000,
                          "change_pct": 5.0}
        rid = self.finish_manual()
        with self.db.sessions() as s:
            row = s.scalar(select(IntradayReading).where(IntradayReading.run_id == rid))
            self.assertEqual(row.data_status, "OK")
            self.assertIsNone(row.quote_time)
            self.assertEqual(row.snapshot["time_basis"], "batch")
            self.assertEqual(row.snapshot["batch_time"], self.now.isoformat())
        self.now += timedelta(minutes=30)
        rid = self.finish_manual()
        with self.db.sessions() as s:
            row = s.scalar(select(IntradayReading).where(IntradayReading.run_id == rid))
            self.assertEqual(row.data_status, "DATA_UNAVAILABLE")
            self.assertEqual(s.scalar(select(func.count(RiskTransition.id))), 1)

    def test_symbol_approval_keeps_unverified_b_share_close_only(self):
        with self.db.sessions.begin() as s:
            s.add(Holding(symbol="900948.SH", market="B"))
        with patch.dict(os.environ, {"INTRADAY_VALIDATED": "false",
                                  "INTRADAY_VERIFIED_SYMBOLS": "600519.SH"}):
            self.price = 21.
            rid = self.finish_manual()
            with self.db.sessions() as s:
                rows = s.scalars(select(IntradayReading).where(IntradayReading.run_id == rid)
                                 .order_by(IntradayReading.symbol)).all()
                self.assertEqual([(r.symbol, r.data_status) for r in rows],
                                 [("600519.SH", "OK"), ("900948.SH", "CLOSE_ONLY")])
                self.assertEqual((s.get(CheckRun, rid).success_count, s.get(CheckRun, rid).error_count), (1, 0))
            self.assertEqual(self.quotes.call_args.args[1], [("600519.SH", "A")])
            self.assertEqual(plan_status(self.db, self.now)["intraday_count"], 1)

    def test_unchanged_volume_for_trading_hour_marks_quote_stale(self):
        self.price = 21.
        self.overrides = {"volume": 1000, "batch_time": self.now.isoformat()}
        self.finish_manual()
        self.now += timedelta(hours=1)
        self.overrides["batch_time"] = self.now.isoformat()
        rid = self.finish_manual()
        with self.db.sessions() as s:
            row = s.scalar(select(IntradayReading).where(IntradayReading.run_id == rid))
            self.assertEqual(row.data_status, "DATA_UNAVAILABLE")
            self.assertIn("成交量", row.reason)
            self.assertEqual(s.scalar(select(func.count(RiskTransition.id))), 1)

    def test_invalid_data_never_confirms_or_clears(self):
        self.price=21.
        self.finish_manual()
        for invalid in ({"quote_time":"2026-11-19T15:00:00+08:00"}, {"quote_time":"2026-11-20T09:00:00+08:00"},
                {"adjustment_verified":False},{"prev_close":19.8},{"currency":"EUR"},{"price":float("nan")},
                {"low":30.},{"open":True},{"error":"HTTP 429"}):
            self.overrides=invalid
            self.now += timedelta(minutes=1)
            rid=self.finish_manual()
            with self.db.sessions() as s:
                row=s.scalars(select(IntradayReading).where(IntradayReading.run_id==rid)).one()
                self.assertIsNone(row.rsi6)
                self.assertEqual(row.status,"DATA_UNAVAILABLE")
                self.assertEqual(s.scalar(select(func.count(RiskTransition.id))),1)
        self.sender.assert_called_once()

    def test_prior_formal_window_ignores_older_adjusted_archive(self):
        with self.db.sessions.begin() as s:
            s.add(DailyBar(symbol="600519.SH", bar_date="2025-12-31", provider="hithink", adjust="forward",
                           open=300., high=300., low=300., close=300., volume=1200.,
                           fetched_at="2025-12-31T15:45:00+08:00"))
        self.price = 21.
        rid = self.finish_manual()
        with self.db.sessions() as s:
            row = s.scalars(select(IntradayReading).where(IntradayReading.run_id == rid)).one()
            self.assertEqual(row.data_status, "OK")
            self.assertEqual(row.snapshot["previous_date"], "2026-11-19")
            self.assertEqual(row.snapshot["history_start"], self.raw_rows[0]["date"])
            self.assertEqual(row.snapshot["previous_adjusted_close"], 20.)
            transition = s.scalars(select(RiskTransition).where(RiskTransition.run_id == rid)).one()
            self.assertEqual(transition.evidence["prev_rsi6"], 50.)
            self.assertEqual(row.rsi6, 100.)

    def test_close_refresh_replaces_only_its_price_basis_and_window(self):
        self.now = self.now.replace(hour=15, minute=45)
        with self.db.sessions.begin() as s:
            for day, adjust in (("2025-12-31", "forward"), ("2026-11-19", "raw")):
                s.add(DailyBar(symbol="600519.SH", bar_date=day, provider="hithink", adjust=adjust,
                               open=300., high=300., low=300., close=300., volume=1000.,
                               fetched_at=day + "T15:45:00+08:00"))
        # A later forward-adjusted fetch revises every price within the loaded window.
        for bar in self.raw_rows:
            bar.update(open=10., high=10., low=10., close=10.)
        self.raw_rows.append({"date":"2026-11-20", "open":10., "high":11., "low":10., "close":11., "volume":1000.})
        self.finish_manual()
        self.now += timedelta(minutes=1)
        calls = self.history.call_count
        cached_run = self.finish_manual()
        self.assertFalse(any(call.args[0] for call in self.history.call_args_list[calls:]))
        with self.db.sessions() as s:
            self.assertEqual(s.get(CheckRun, cached_run).state, "SUCCESS")
            rows = s.scalars(select(DailyBar)).all()
            prices = {(b.bar_date, b.adjust):b.close for b in rows}
            self.assertEqual(len(prices), 108)
            self.assertEqual(prices["2025-12-31", "forward"], 300.)
            self.assertEqual(prices["2026-11-19", "raw"], 300.)
            self.assertEqual(prices["2026-11-19", "forward"], 10.)
            self.assertEqual(prices["2026-11-20", "forward"], 11.)
            self.assertEqual(s.get(Holding, 1).rsi6, 100.)

    def test_unvalidated_manual_refresh_uses_formal_close_scanner(self):
        self.history.reset_mock()
        with patch.dict(os.environ, {"INTRADAY_VALIDATED": "false"}):
            run_id = self.finish_manual()
        self.history.assert_called_once()
        self.assertEqual(self.history.call_args.args[0], [("600519.SH", "A")])
        self.assertEqual(self.history.call_args.args[2].isoformat(), "2026-11-19")
        self.quotes.assert_not_called()
        with self.db.sessions() as session:
            run = session.get(CheckRun, run_id)
            self.assertEqual((run.trigger_type, run.state, run.success_count, run.error_count),
                             ("manual", "SUCCESS", 1, 0))
            holding = session.get(Holding, 1)
            self.assertEqual((holding.data_status, holding.bar_date, holding.rsi6), ("OK", "2026-11-19", 50))
            self.assertEqual(session.scalar(select(func.count(IntradayReading.id))), 0)

    def test_default_gate_retains_daily_brief_without_outbound_fetch(self):
        with patch.dict(os.environ, {"INTRADAY_VALIDATED":"false"}):
            self.service.scheduled_tick("intraday_clock")
            self.assertEqual(plan_status(self.db,self.now)["planned_today"],2)
            self.now=self.now.replace(hour=14,minute=30)
            for _ in range(2):
                self.service.scheduled_tick("intraday_clock")
                self.wait()
            with self.db.sessions() as s:
                self.assertEqual(s.scalar(select(func.count(DailyBrief.id))),1)
                row=s.scalars(select(IntradayReading)).one()
                self.assertEqual(row.data_status,"CLOSE_ONLY")
                self.assertEqual(s.scalars(select(DailyBrief)).one().trade_date,"2026-11-20")
            self.quotes.assert_not_called()
            self.sender.assert_called_once()

    def test_same_slot_uses_one_fetch_then_reboot_and_no_holiday_scans(self):
        self.now=self.now.replace(hour=14,minute=30)
        self.service.scheduled_tick("intraday_clock")
        self.wait()
        new=ScanService(self.db, fetch=self.history, snapshot_fetch=self.quotes, clock=lambda:self.now,sender=self.sender)
        self.service.stop()
        new.start()
        try:
            for dt in (self.now,self.now.replace(day=21),self.now.replace(month=10,day=1),self.now.replace(year=2028)):
                self.now=dt
                new.scheduled_tick("intraday_clock")
            new.pool.submit(lambda:None).result()
        finally:
            new.stop()
        self.quotes.assert_called_once()
        self.assertEqual(self.quotes.call_args.args[-1],"brief")

    def test_high_cross_links_prior_and_current_evidence(self):
        # Same prior formal close for every provisional decision.
        with self.db.sessions.begin() as s:
            h=s.get(Holding,1)
            run=CheckRun(trigger_type="intraday")
            s.add(run)
            s.flush()
            info={"prev_rsi6":76.2,"prev_rsi12":73.,"rsi6":68.,"rsi12":70.}
            from stock_radar.rules.rsi import evaluate_rsi_v1
            evaluated=evaluate_rsi_v1(info["prev_rsi6"], info["prev_rsi12"],info["rsi6"],info["rsi12"])
            record_transitions(s,h,run.id,"2026-11-20",evaluated,info,self.now)
            row=s.scalars(select(RiskTransition)).one()
            self.assertEqual((row.severity,row.stage,row.evidence["prev_rsi12"]),("ALERT","PROVISIONAL",73.))

    def test_migration_v1_roundtrip_preserves_business_rows(self):
        cfg=Config()
        cfg.set_main_option("script_location",str(Path(__file__).resolve().parents[1]/"backend"/"migrations"))
        with self.db.engine.begin() as conn:
            cfg.attributes["connection"]=conn
            command.downgrade(cfg,"0001")
            self.assertNotIn("intraday_readings",inspect(conn).get_table_names())
            self.assertEqual(conn.execute(text("select count(*) from daily_bars")).scalar_one(),105)
            self.assertEqual(conn.execute(text("select count(*) from holdings")).scalar_one(),1)
            command.upgrade(cfg,"head")
            self.assertEqual(conn.execute(text("select intraday_enabled,brief_time from settings")).one(),(0,"14:30"))
            self.assertFalse(conn.execute(text("pragma foreign_key_check")).all())

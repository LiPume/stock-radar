"""V1.1 notice behavior against private temporary SQLite databases and a mock sender."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import func, select
from backend.app.briefings import ALERT_TITLE, BRIEF_TITLE, notification_status
from backend.app.db import (
    CheckRun,
    DailyBar,
    DailyBrief,
    DB,
    Delivery,
    Holding,
    IntradayReading,
    RiskTransition,
    Setting,
)
from backend.app.notifications import deliver_batch
from scripts.market_probe import SHANGHAI


class BriefingTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(TemporaryDirectory())
        self.url = "sqlite:///" + str(Path(tmp) / "notice.db")
        self.db = DB(self.url)
        self.addCleanup(self.db.engine.dispose)
        self.db.migrate()
        self.now = datetime(2026, 11, 20, 14, 30, tzinfo=SHANGHAI)
        self.sender = Mock(return_value=("SENT", "0", None))
        self.enterContext(patch.dict(os.environ, {"SERVERCHAN_SENDKEY": "SCTfake"}))
        with self.db.sessions.begin() as s:
            s.get(Setting, 1).notifications_enabled = True
            self.stock = Holding(symbol="600519.SH", market="A", display_name="用户别名")
            s.add(self.stock)
            s.flush()

    def run_row(self, **kwargs):
        with self.db.sessions.begin() as s:
            row = CheckRun(trigger_type="manual", started_at=self.now.isoformat(), **kwargs)
            s.add(row)
            s.flush()
            return row.id

    def reading(self, run_id, status="NO_RULE_TRIGGERED"):
        with self.db.sessions.begin() as s:
            s.add(
                IntradayReading(
                    holding_id=self.stock.id,
                    run_id=run_id,
                    symbol=self.stock.symbol,
                    trade_date=self.now.date().isoformat(),
                    observed_at=self.now.isoformat(),
                    quote_time=self.now.isoformat(),
                    currency="CNY",
                    name="模拟公司",
                    provider="fixture",
                    price=35.31,
                    change_pct=1.54,
                    rsi6=73.1,
                    rsi12=74.5,
                    rsi24=70.1,
                    data_status="OK",
                    status=status,
                )
            )

    def event(self, run_id, stage="PROVISIONAL", parent=None):
        with self.db.sessions.begin() as s:
            row = RiskTransition(
                holding_id=self.stock.id,
                symbol=self.stock.symbol,
                trade_date=self.now.date().isoformat(),
                rule_code="RSI_HIGH_DEATH_CROSS",
                run_id=run_id,
                severity="ALERT",
                stage=stage,
                parent_id=parent,
                created_at=self.now.isoformat(),
                evidence={
                    "prev_rsi6": 78.2,
                    "prev_rsi12": 74.3,
                    "rsi6": 73.1,
                    "rsi12": 74.5,
                    "overbought": 80,
                },
            )
            s.add(row)
            s.flush()
            return row.id

    def send(self, run_id, daily=False, db=None):
        deliver_batch(db or self.db, run_id, self.now, sender=self.sender, daily_brief=daily)

    def test_complete_healthy_brief_and_restart(self):
        run = self.run_row()
        self.reading(run)
        self.send(run, daily=True)
        reopened = DB(self.url)
        self.addCleanup(reopened.engine.dispose)
        self.send(self.run_row(), daily=True, db=reopened)
        self.sender.assert_called_once()
        with reopened.sessions() as s:
            report = s.scalars(select(DailyBrief)).one()
            entry = report.items[0]
            self.assertEqual(
                (report.title, entry["status"], entry["name"]), (BRIEF_TITLE, "NO_RULE_TRIGGERED", "模拟公司")
            )
            self.assertIn("今日 +1.54%", report.body)
            self.assertIn("数据异常 0", report.body)
            status = notification_status(s, self.now)
            self.assertEqual((status["used"], status["remaining"], status["brief_status"]), (1, 4, "SENT"))

    def test_all_failures_still_report_all_enabled_and_show_last_close(self):
        with self.db.sessions.begin() as s:
            s.add_all(
                [
                    Holding(symbol="900901.SH", market="B"),
                    Holding(symbol="200001.SZ", market="B"),
                    Holding(symbol="600001.SH", market="A", enabled=False),
                    Holding(symbol="600002.SH", market="A", deleted=True),
                ]
            )
            s.add(
                DailyBar(
                    symbol="900901.SH",
                    bar_date="2026-11-19",
                    provider="fixture",
                    adjust="qfq",
                    fetched_at=self.now.isoformat(),
                    open=1,
                    high=1.1,
                    low=0.9,
                    close=1.02,
                    volume=700,
                    rsi6=42,
                    rsi12=45,
                    rsi24=50,
                )
            )
        run = self.run_row()
        self.send(run, daily=True)
        self.sender.assert_called_once()
        with self.db.sessions() as s:
            report = s.scalars(select(DailyBrief)).one()
            self.assertEqual(len(report.items), 3)
            self.assertTrue(all(item["status"] == "ERROR" for item in report.items))
            self.assertIn("2026-11-19", report.body)
            self.assertIn("1.02 USD", report.body)
            self.assertIn("HKD", report.body)
            self.assertNotIn("用户别名", report.body)
            self.assertNotIn("600002.SH", report.body)
            self.assertIn("今日涨跌待核实", report.body)
            self.assertIn("全部持仓", report.body)

    def test_alert_in_brief_is_one_send_and_close_confirmation_is_linked(self):
        run = self.run_row()
        self.reading(run, "ALERT")
        risk = self.event(run)
        self.send(run, daily=True)
        title, body = self.sender.call_args.args
        self.assertEqual(title, BRIEF_TITLE)
        self.assertTrue(body.startswith("本轮新触发风险"))
        for point in ("78.20", "73.10", "74.30", "74.50"):
            self.assertIn(point, body)
        close = self.run_row()
        self.event(close, "CONFIRMED", risk)
        self.send(close)
        self.sender.assert_called_once()
        with self.db.sessions() as s:
            rows = s.scalars(select(Delivery).order_by(Delivery.id)).all()
            self.assertEqual([r.status for r in rows], ["SENT", "SKIPPED"])
            self.assertIn("同一风险", rows[1].reason)

    def test_reserve_report_slot_and_record_suppression(self):
        with self.db.sessions.begin() as s:
            s.get(Setting, 1).message_daily_limit = 1
        run = self.run_row()
        self.event(run)
        self.send(run)
        self.sender.assert_not_called()
        self.send(self.run_row(), daily=True)
        self.sender.assert_called_once()
        with self.db.sessions() as s:
            blocked = s.scalar(select(Delivery).where(Delivery.run_id == run))
            self.assertIn("预留", blocked.reason)
            self.assertEqual(notification_status(s, self.now)["used"], 1)
            self.assertEqual(s.scalar(select(func.count(RiskTransition.id))), 1)

    def test_retrigger_cooldown_and_unknown_receipt(self):
        self.sender.return_value = ("UNKNOWN", "NETWORK", "回执未知")
        run = self.run_row()
        first = self.event(run)
        self.send(run)
        clear = self.run_row()
        self.event(clear, "CLEARED", first)
        self.send(clear)
        self.now += timedelta(minutes=30)
        repeat = self.run_row()
        self.event(repeat)
        self.send(repeat)
        self.sender.assert_called_once()
        with self.db.sessions() as s:
            self.assertIn("冷却", s.scalar(select(Delivery).where(Delivery.run_id == repeat)).reason)
        self.now += timedelta(minutes=31)
        later = self.run_row()
        self.event(later)
        self.send(later)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.sender.call_args.args[0], ALERT_TITLE)

    def test_concurrent_brief_claim_reserves_one_attempt(self):
        runs = [self.run_row(), self.run_row()]
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda run: self.send(run, daily=True), runs))
        self.sender.assert_called_once()
        with self.db.sessions() as s:
            self.assertEqual(s.scalar(select(func.count(DailyBrief.id))), 1)

    def test_unknown_exception_is_audited_without_retry(self):
        self.sender.side_effect = TimeoutError("sensitive details")
        self.send(self.run_row(), daily=True)
        self.sender.assert_called_once()
        with self.db.sessions() as s:
            row = s.scalars(select(Delivery)).one()
            self.assertEqual((row.attempts, row.status, row.response_code), (1, "UNKNOWN", "NO_RECEIPT"))
            self.assertNotIn("sensitive", row.reason)

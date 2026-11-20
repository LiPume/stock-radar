"""Offline integration coverage for scheduler, market adapters, SQLite and delivery."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from pathlib import Path
import io
import json
import subprocess
from urllib.error import HTTPError, URLError
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import func, select

from backend.app.db import CheckRun, DB, DailyBar, Delivery, Holding, Setting, SignalEvent
from backend.app import notifications, providers
from backend.app.scanner import ScanBusy, ScanCooldown, ScanService
from backend.app.scheduler import make_scheduler
from scripts import market_probe as probe


def sample_rows(end: date, *, stale=False) -> list[dict]:
    day = end - timedelta(days=int(stale))
    days = []
    while len(days) < 105:
        if day.weekday() < 5 and not any(day.month == m and a <= day.day <= b for m, a, b in probe.HOLIDAYS_2026):
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    return [{"date": d.isoformat(), "open": price, "high": price, "low": price,
             "close": price, "volume": 1000} for d, price in zip(days, [20.0] * 104 + [21.0])]


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = DB("sqlite:///" + str(Path(tmp.name) / "test.db"))
        self.addCleanup(self.db.engine.dispose)
        self.db.migrate()
        self.instant = datetime(2026, 11, 20, 15, 45, tzinfo=probe.SHANGHAI)
        env = patch.dict(os.environ, {"SCHEDULER_ENABLED": "false", "HITHINK_FINANCE_API_KEY": "fixture",
                                       "SERVERCHAN_SENDKEY": "SCTfixture"})
        env.start()
        self.addCleanup(env.stop)
        fake_clock = patch.object(probe, "current_time", side_effect=lambda: self.instant)
        fake_clock.start()
        self.addCleanup(fake_clock.stop)
        self.requested = []
        self.bad = {}

        def fetch(stocks, start, end):
            self.requested.extend(stocks)
            for symbol, market in stocks:
                yield symbol, sample_rows(end, stale=self.bad.get(symbol) == "stale"), self.bad.get(symbol) if self.bad.get(symbol) != "stale" else None

        self.sender = Mock(return_value=("SENT", "0", None))
        self.service = ScanService(self.db, fetch=fetch, sender=self.sender, clock=lambda: self.instant)
        self.service.start()
        self.addCleanup(self.service.stop)

    def holding(self, symbol, market="A") -> None:
        with self.db.sessions.begin() as session:
            session.add(Holding(symbol=symbol, market=market))
            session.get(Setting, 1).notifications_enabled = True

    def wait_run(self, run_id):
        self.service.pool.submit(lambda: None).result(timeout=10)
        with self.db.sessions() as session:
            return session.get(CheckRun, run_id)

    def test_two_stocks_share_one_push_and_idempotent_cache(self):
        self.holding("600519.SH")
        self.holding("900901.SH", "B")
        run = self.wait_run(self.service.trigger())
        self.assertEqual((run.state, run.success_count), ("SUCCESS", 2))
        self.assertEqual(self.sender.call_count, 1)
        self.assertIn("900901.SH", self.sender.call_args.args[1])
        self.assertIn("600519.SH", self.sender.call_args.args[1])
        with self.assertRaises(ScanCooldown):
            self.service.trigger()
        self.instant += timedelta(minutes=1)
        self.wait_run(self.service.trigger())
        self.assertEqual(len(self.requested), 2)
        self.assertEqual(self.sender.call_count, 1)
        with self.db.sessions() as s:
            self.assertEqual(s.scalar(select(func.count(SignalEvent.id))), 2)
            self.assertEqual(s.scalar(select(func.count(DailyBar.id))), 210)
            batch = s.scalars(select(Delivery)).one()
            self.assertEqual((len(batch.event_ids), batch.attempts, batch.status), (2, 1, "SENT"))
            self.assertEqual(s.scalars(select(Holding.status)).all(), ["WATCH", "WATCH"])

    def test_stale_and_auth_failures_preserve_history_no_new_event(self):
        self.holding("900901.SH", "B")
        self.holding("600519.SH")
        self.assertEqual(self.wait_run(self.service.trigger()).success_count, 2)
        self.bad = {"900901.SH": "stale", "600519.SH": "AUTH: missing key"}
        self.instant += timedelta(minutes=30)
        run = self.wait_run(self.service.trigger())
        self.assertEqual((run.state, run.error_count), ("FAILED", 2))
        with self.db.sessions() as s:
            self.assertEqual(s.scalar(select(func.count(SignalEvent.id))), 2)
            b = s.scalar(select(Holding).where(Holding.market == "B"))
            self.assertEqual((b.data_status, b.status, b.rsi6), ("STALE", "STALE", None))
            self.assertEqual(s.scalar(select(func.count(DailyBar.id))), 210)
        self.assertEqual(self.sender.call_count, 1)
        self.service.scheduled_tick("retry_1")
        self.service.pool.submit(lambda: None).result(timeout=10)
        self.assertEqual(self.requested[-1], ("900901.SH", "B"))
        before = len(self.requested)
        self.service.scheduled_tick("retry_1")
        self.assertEqual(len(self.requested), before)

    def test_source_failures_are_isolated(self):
        self.holding("600519.SH")
        self.holding("900901.SH", "B")
        self.bad["600519.SH"] = "HTTP_ERROR: status=403"
        run = self.wait_run(self.service.trigger())
        self.assertEqual((run.state, run.success_count, run.error_count), ("PARTIAL", 1, 1))
        self.assertNotIn("600519.SH", self.sender.call_args.args[1])

    def test_ambiguous_push_is_never_repeated(self):
        self.holding("600519.SH")
        self.sender.return_value = ("UNKNOWN", "NETWORK", "无回执")
        self.wait_run(self.service.trigger())
        self.instant += timedelta(minutes=10)
        self.wait_run(self.service.trigger())
        with self.db.sessions() as session:
            row = session.scalars(select(Delivery)).one()
            self.assertEqual((row.status, row.attempts), ("UNKNOWN", 1))
        self.assertEqual(self.sender.call_count, 1)

    def test_changing_threshold_keeps_audit_but_does_not_resend_same_cross(self):
        self.holding("600519.SH")
        self.wait_run(self.service.trigger())
        with self.db.sessions.begin() as session:
            session.get(Setting, 1).overbought = 81
        self.instant += timedelta(minutes=1)
        self.wait_run(self.service.trigger())
        with self.db.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(SignalEvent.id))), 2)
            self.assertEqual(session.scalars(select(Delivery.status).order_by(Delivery.id)).all(),
                             ["SENT", "SKIPPED"])
        self.sender.assert_called_once()

    def test_restart_records_interrupted_scan_without_replaying_network(self):
        self.holding("600519.SH")
        self.wait_run(self.service.trigger())
        with self.db.sessions.begin() as session:
            row = session.scalars(select(CheckRun)).one()
            row.state = "RUNNING"
            delivery = session.scalars(select(Delivery)).one()
            delivery.status = "SENDING"
        self.service.stop()
        restarted = ScanService(self.db, sender=self.sender, clock=lambda: self.instant)
        restarted.start()
        try:
            with self.db.sessions() as session:
                self.assertEqual(session.scalars(select(CheckRun.state)).one(), "FAILED")
                self.assertEqual(session.scalars(select(Delivery.status)).one(), "UNKNOWN")
            self.sender.assert_called_once()
        finally:
            restarted.stop()

    def test_connection_refusal_has_bounded_retry(self):
        self.holding("600519.SH")
        self.sender.return_value = ("FAILED", "CONNECT_FAILED", "连接失败")
        with patch.object(notifications, "sleep"):
            self.wait_run(self.service.trigger())
        with self.db.sessions() as session:
            self.assertEqual(session.scalars(select(Delivery)).one().attempts, 2)
        self.assertEqual(self.sender.call_count, 2)

    def test_clock_calendar_and_single_owner(self):
        other = ScanService(self.db)
        with self.assertRaisesRegex(RuntimeError, "单个 worker"):
            other.start()
        other.stop()
        self.instant = self.instant.replace(month=10, day=1)
        self.service.scheduled_tick("scheduled")
        self.assertEqual(len(make_scheduler(self.service).get_jobs()), 3)
        with self.db.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(CheckRun.id))), 0)

    def test_concurrent_refresh_is_rejected(self):
        entered, release = Event(), Event()
        def fetch(stocks, start, end):
            entered.set()
            release.wait(3)
            return []
        self.service.fetch = fetch
        run_id = self.service.trigger()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(ScanBusy):
                self.service.trigger()
        finally:
            release.set()
        self.assertEqual(self.wait_run(run_id).state, "SUCCESS")

    def test_weekend_manual_check_does_not_deliver_previous_day(self):
        self.holding("600519.SH")
        self.instant += timedelta(days=1)
        self.wait_run(self.service.trigger())
        self.sender.assert_not_called()
        with self.db.sessions() as session:
            self.assertEqual(session.scalars(select(Delivery.status)).one(), "SKIPPED")

    def test_missing_preceding_session_does_not_trigger(self):
        self.holding("600519.SH")
        def missing(stocks, start, end):
            for symbol, _market in stocks:
                rows = sample_rows(end)
                del rows[-2]
                yield symbol, rows, None
        self.service.fetch = missing
        self.assertEqual(self.wait_run(self.service.trigger()).error_count, 1)
        self.sender.assert_not_called()

    def test_unreturned_symbol_and_unexpected_adapter_failure(self):
        self.holding("600519.SH")
        self.service.fetch = Mock(return_value=[])
        self.assertEqual(self.wait_run(self.service.trigger()).state, "FAILED")
        self.instant += timedelta(minutes=1)
        self.service.fetch = Mock(side_effect=ValueError("private detail"))
        run = self.wait_run(self.service.trigger())
        self.assertIn("UPSTREAM_ERROR", run.error_summary)
        self.assertNotIn("private detail", run.error_summary)
        self.sender.assert_not_called()

    def test_daily_quota_and_notifications_disabled(self):
        self.holding("600519.SH")
        with self.db.sessions.begin() as s:
            old = CheckRun(trigger_type="scheduled", state="SUCCESS")
            s.add(old)
            s.flush()
            s.add(Delivery(run_id=old.id, status="FAILED", attempts=5, event_ids=[],
                           created_at=self.instant.isoformat()))
        self.wait_run(self.service.trigger())
        with self.db.sessions() as s:
            self.assertIn("额度", s.scalars(select(Delivery).order_by(Delivery.id.desc())).first().reason)
        self.sender.assert_not_called()
        self.holding("900901.SH", "B")
        with self.db.sessions.begin() as s:
            s.get(Setting, 1).notifications_enabled = False
        self.instant += timedelta(minutes=1)
        self.wait_run(self.service.trigger())
        with self.db.sessions() as s:
            self.assertIn("关闭", s.scalars(select(Delivery).order_by(Delivery.id.desc())).first().reason)
        self.sender.assert_not_called()

    def test_akshare_worker_timeout(self):
        with patch.object(providers.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 45)):
            with self.assertRaisesRegex(RuntimeError, "NETWORK_UNAVAILABLE"):
                providers.b_history("900901.SH", self.instant.date(), self.instant.date())

    def test_serverchan_http_receipt_and_failure_categories(self):
        opener = Mock()
        with patch.object(notifications, "build_opener", return_value=opener):
            def reply(obj):
                opener.open.return_value = io.BytesIO(json.dumps(obj).encode())
                return notifications.send_message("测试", "模拟内容")
            self.assertEqual(reply({"code": 0})[:2], ("SENT", "0"))
            self.assertEqual(reply({"code": 7})[:2], ("FAILED", "7"))
            self.assertEqual(reply({"other": {} })[0], "UNKNOWN")
            opener.open.side_effect = HTTPError("https://example.invalid", 429, "limit", {}, io.BytesIO(b""))
            self.assertEqual(notifications.send_message("测试", "")[:2], ("FAILED", "429"))
            opener.open.side_effect = URLError(ConnectionRefusedError())
            self.assertEqual(notifications.send_message("测试", "")[:2], ("FAILED", "CONNECT_FAILED"))
            opener.open.side_effect = TimeoutError()
            self.assertEqual(notifications.send_message("测试", "")[0], "UNKNOWN")

    def test_provider_batch_splits_and_reports_each_missing_group(self):
        symbols = [f"{600000+i}.SH" for i in range(11)]
        capture = Mock(return_value=({"code": 0, "data": {"interval": "1d", "adjust": "forward", "item": []}}, {}))
        with patch.object(probe, "fetch_hithink", capture):
            rows = list(providers.fetch_history([(s, "A") for s in symbols], date(2026, 3, 25), self.instant.date()))
        self.assertEqual([len(args.args[0]) for args in capture.call_args_list], [10, 1])
        self.assertTrue(all(error.startswith("SYMBOL_MISSING") for _, _, error in rows))

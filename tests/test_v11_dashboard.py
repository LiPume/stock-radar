"""Compatibility and read-only mobile access to V1.1 data."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.db import CheckRun, DailyBar, Holding, IntradayReading
from backend.app.main import create_app
from scripts.market_probe import SHANGHAI


class IdleScanner:
    def __init__(self, db): pass
    def start(self): pass
    def stop(self): pass


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.app = create_app("sqlite:///" + self.temp + "/db.sqlite", "fixture", IdleScanner)
        self.client = self.enterContext(TestClient(self.app))
        from backend.app.auth import Auth
        self.app.state.auth.tokens[Auth.digest("test-token")] = float("inf")
        self.headers = {"Authorization": "Bearer test-token"}

    def test_private_history_usage_and_old_client_settings(self):
        self.assertEqual(self.client.get("/api/v1/history").status_code, 401)
        history = self.client.get("/api/v1/history", headers=self.headers).json()["data"]
        self.assertEqual(history, {"briefings": [], "deliveries": [], "transitions": []})
        response = self.client.get("/api/v1/system/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        status = response.json()["data"]
        self.assertEqual(status["messages"]["remaining"], 5)
        self.assertEqual(status["sources"]["hithink"]["today"]["http_requests"], 0)
        url = "/api/v1/settings"
        legacy = {"overbought": 80, "high_level": 70, "notifications_enabled": False}
        with patch.dict("os.environ", {"INTRADAY_VALIDATED": "false"}):
            self.assertEqual(self.client.put(url, headers=self.headers,
                json={**legacy, "intraday_enabled": True}).status_code, 409)
        for invalid in ({"brief_time":"12:15"},{"interval_minutes":45},{"message_daily_limit":0}):
            self.assertEqual(self.client.put(url, headers=self.headers, json={**legacy, **invalid}).status_code, 422)
        self.client.put(url, headers=self.headers, json={**legacy,"brief_time":"13:45","interval_minutes":60}).raise_for_status()
        updated = self.client.put(url, headers=self.headers, json=legacy).json()["data"]
        self.assertEqual((updated["brief_time"],updated["interval_minutes"]), ("13:45",60))

    def test_intraday_error_shows_dated_close_and_never_ok(self):
        with self.app.state.db.sessions.begin() as s:
            h = Holding(symbol="600000.SH", market="A", data_status="OK", status="NO_RULE_TRIGGERED",bar_date="2026-11-19")
            run = CheckRun(trigger_type="brief")
            s.add_all([h,run]); s.flush()
            s.add(DailyBar(symbol=h.symbol,bar_date=h.bar_date,provider="hithink",adjust="forward",
                           open=10,close=10.1,high=11,low=9,volume=5,fetched_at=h.bar_date,rsi6=59,rsi12=58,rsi24=57))
            s.add(IntradayReading(holding_id=h.id,symbol=h.symbol,run_id=run.id,trade_date="2026-11-20",
                    observed_at="2026-11-20T14:30:00+08:00", status="DATA_UNAVAILABLE",
                    data_status="DATA_UNAVAILABLE",reason="无有效快照",provider="hithink"))
        with patch("backend.app.main.current_time", return_value=datetime(2026,11,20,14,31,tzinfo=SHANGHAI)):
            item = self.client.get("/api/v1/holdings",headers=self.headers).json()["data"][0]
        self.assertEqual((item["data_status"],item["price_context"],item["quote_time"],item["price"],item["rsi6"]),
                         ("DATA_UNAVAILABLE","LAST_CLOSE","2026-11-19",10.1,None))
        self.assertEqual(item["close_rsi"]["rsi6"], 59)

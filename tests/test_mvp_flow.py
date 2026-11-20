"""Drive the full API + SQLite + scanner flow using an offline provider response."""
from datetime import datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from backend.app.db import Base
from backend.app.main import create_app
from backend.app.scanner import ScanService
from scripts import market_probe as probe
from test_scan import sample_rows


class MvpFlowTest(unittest.TestCase):
    def test_api_check_history_repeat_and_provider_failure(self):
        now = datetime(2026, 11, 20, 16, 00, tzinfo=probe.SHANGHAI)
        clock = lambda: now
        env = {"SCHEDULER_ENABLED": "false", "HITHINK_FINANCE_API_KEY": "offline-fixture",
               "SERVERCHAN_SENDKEY": "SCTfixture"}
        self.enterContext(patch.dict(os.environ, env))
        self.enterContext(patch.object(probe, "current_time", side_effect=clock))
        self.enterContext(patch("backend.app.main.current_time", side_effect=clock))
        sender = Mock(return_value=("SENT", "0", None))
        source_bars = sample_rows(now.date())
        group = {"thscode": "600519.SH", "item": [
            {"date_ms": probe.ms_from_local_date(datetime.fromisoformat(r["date"]).date()), "volume": r["volume"],
             **{k + "_price": r[k] for k in ("open", "high", "low", "close")}} for r in source_bars
        ]}
        source = self.enterContext(patch.object(probe, "fetch_hithink", return_value=(
            {"code": 0, "data": {"interval": "1d", "adjust": "forward", "item": [group]}},
            {"http_status": 200},
        )))
        tmp = self.enterContext(TemporaryDirectory())
        pw = PasswordHasher(memory_cost=8192, time_cost=1).hash("workflow-fixture")
        app = create_app("sqlite:///" + str(Path(tmp) / "flow.db"), pw,
                         scanner_factory=lambda db: ScanService(db, sender=sender, clock=clock))
        client = self.enterContext(TestClient(app))
        with app.state.db.engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        self.assertEqual(differences, [])

        def data(method, path, status=200, **kwargs):
            result = client.request(method, "/api/v1" + path, **kwargs)
            self.assertEqual(result.status_code, status, result.text)
            self.assertIsNone(result.json()["error"])
            return result.json()["data"]

        def run():
            run_id = data("POST", "/checks", 202)["run_id"]
            deadline = monotonic() + 4
            while monotonic() < deadline:
                row = data("GET", f"/checks/{run_id}")
                if row["finished_at"] is not None:
                    return row
                sleep(0.01)
            self.fail("Worker did not finish its offline scan")

        self.assertEqual(client.post("/api/v1/checks").status_code, 401)
        login = data("POST", "/auth/login", json={"password": "workflow-fixture"})
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        config = data("GET", "/settings")
        config["notifications_enabled"] = True
        self.assertEqual(data("PUT", "/settings", json=config), config)
        holding = data("POST", "/holdings", 201, json={"symbol": "600519.SH", "market": "A"})
        self.assertEqual(holding["data_status"], "PENDING")

        self.assertEqual(run()["state"], "SUCCESS")
        h = data("GET", "/holdings")[0]
        self.assertEqual((h["status"], h["data_status"], h["provider"]), ("WATCH", "OK", "hithink"))
        chart = data("GET", "/stocks/600519.SH/bars")
        self.assertEqual(len(chart), 105)
        self.assertEqual(chart[-1]["rsi6"], h["rsi6"])
        signals = data("GET", "/signals?symbol=600519.SH")
        self.assertEqual([event["rule_code"] for event in signals], ["RSI_OVERBOUGHT_CROSS"])
        self.assertEqual(data("GET", "/system/status")["latest_delivery"]["status"], "SENT")

        self.assertEqual(client.post("/api/v1/checks").status_code, 429)
        now += timedelta(seconds=31)
        self.assertEqual(run()["state"], "SUCCESS")
        self.assertEqual(source.call_count, 1)
        self.assertEqual(data("GET", "/signals"), signals)
        now += timedelta(minutes=6)
        source.side_effect = RuntimeError("HTTP_ERROR: status=429")
        failed = run()
        self.assertEqual((failed["state"], failed["error_count"]), ("FAILED", 1))
        self.assertEqual(data("GET", "/system/status")["latest_run"]["id"], failed["id"])
        current = data("GET", "/holdings")[0]
        self.assertEqual(current["data_status"], "DATA_UNAVAILABLE")
        self.assertIsNone(current["rsi6"])
        self.assertEqual(data("GET", "/signals"), signals)
        self.assertEqual(data("GET", "/stocks/600519.SH/bars"), chart)
        sender.assert_called_once()

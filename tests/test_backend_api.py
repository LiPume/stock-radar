"""API tests with a real migrated SQLite file and no external I/O."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db import DailyBar, Holding
from backend.app.main import create_app


class IdleScanner:
    def __init__(self, db):
        self.db = db

    def start(self):
        pass

    def stop(self):
        pass


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hash = PasswordHasher(time_cost=1, memory_cost=8192).hash("fixture-password")

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.url = "sqlite:///" + str(Path(folder.name) / "test.db")
        self.app = create_app(self.url, self.hash, IdleScanner)
        self.client = self.enterContext(TestClient(self.app))

    def login(self, client=None):
        client = client or self.client
        response = client.post("/api/v1/auth/login", json={"password": "fixture-password"})
        self.assertEqual(response.status_code, 200)
        client.headers["Authorization"] = "Bearer " + response.json()["data"]["access_token"]

    def add(self):
        response = self.client.post("/api/v1/holdings", json={"symbol": "600519.SH", "market": "A", "notes": "样例"})
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["id"]

    def test_private_routes_require_authentication(self):
        for path in ["holdings", "signals", "settings", "checks/1", "system/status", "stocks/600519.SH/bars"]:
            with self.subTest(path=path):
                res = self.client.get("/api/v1/" + path)
                self.assertEqual(res.status_code, 401)
                self.assertEqual(res.headers["X-Request-ID"], res.json()["meta"]["request_id"])
                self.assertEqual(res.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.post("/api/v1/checks").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/health").json()["data"], {"status": "ok"})

    def test_crud_and_restart(self):
        self.login()
        row_id = self.add()
        path = f"/api/v1/holdings/{row_id}"
        duplicate = self.client.post("/api/v1/holdings", json={"symbol": "600519.SH", "market": "A"})
        self.assertEqual(duplicate.status_code, 409)
        res = self.client.patch(path, json={"notes": "新备注", "enabled": False})
        self.assertFalse(res.json()["data"]["enabled"])
        # New process-level auth state, same on-disk SQLite.
        with TestClient(create_app(self.url, self.hash, IdleScanner)) as second:
            self.login(second)
            self.assertEqual(second.get("/api/v1/holdings").json()["data"][0]["notes"], "新备注")
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/holdings").json()["data"], [])
        self.assertEqual(self.add(), row_id)
        self.assertTrue(self.client.get("/api/v1/holdings").json()["data"][0]["enabled"])
        with self.app.state.db.engine.connect() as c:
            self.assertEqual(c.scalar(text("PRAGMA journal_mode")), "wal")
            self.assertEqual(c.scalar(text("select version_num from alembic_version")), "0001")

    def test_charts_are_ordered_and_old_quotes_are_labelled(self):
        self.login()
        row_id = self.add()
        with self.app.state.db.sessions.begin() as s:
            holding = s.get(Holding, row_id)
            holding.data_status, holding.status, holding.bar_date = "OK", "WATCH", "2025-09-01"
            for d in ["2025-09-02", "2025-09-01", "2025-09-03"]:
                s.add(DailyBar(symbol=holding.symbol, bar_date=d, open=10, high=12, low=9, close=11,
                               volume=300, provider="fixture", adjust="forward", fetched_at=d))
        with patch("backend.app.main.latest_closed_session") as session:
            session.return_value.isoformat.return_value = "2025-09-03"
            self.assertEqual(self.client.get("/api/v1/holdings").json()["data"][0]["data_status"], "STALE")
        rows = self.client.get("/api/v1/stocks/600519.SH/bars?days=2").json()["data"]
        self.assertEqual([r["bar_date"] for r in rows], ["2025-09-02", "2025-09-03"])
        self.assertEqual(self.client.get("/api/v1/stocks/900901.SH/bars").status_code, 404)

    def test_throttling_and_logout(self):
        self.login()
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/holdings").status_code, 401)
        for _ in range(5):
            self.assertEqual(self.client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"password": "fixture-password"}).status_code, 429)

    def test_settings_have_sane_bounds(self):
        self.login()
        with patch.dict("os.environ", {"SERVERCHAN_SENDKEY": ""}):
            v = {"overbought": 80, "high_level": 72, "notifications_enabled": False}
            self.assertEqual(self.client.put("/api/v1/settings", json=v).json()["data"]["high_level"], 72)
            self.assertEqual(self.client.put("/api/v1/settings", json={**v, "high_level": -1}).status_code, 422)
            self.assertEqual(self.client.put("/api/v1/settings", json={**v, "notifications_enabled": True}).status_code, 409)
        for symbol, market in [("900901.SH", "A"), ("600519.SH", "B"), ("600519", "A")]:
            r = self.client.post("/api/v1/holdings", json={"symbol": symbol, "market": market})
            self.assertEqual(r.status_code, 422)

    def test_cross_origin_preflight(self):
        headers = {"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "Authorization, Content-Type"}
        res = self.client.options("/api/v1/checks", headers=headers)
        self.assertEqual(res.headers["Access-Control-Allow-Origin"], headers["Origin"])
        headers["Origin"] = "https://not-an-allowed-origin.example"
        self.assertNotIn("access-control-allow-origin", self.client.options("/api/v1/checks", headers=headers).headers)

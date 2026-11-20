"""Offline checks for the status and request metadata reported by the CLI."""
from contextlib import redirect_stdout
from datetime import date, datetime
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from test_probe import probe


class ProbeRunTest(unittest.TestCase):
    def test_closed_sessions_shanghai(self):
        for month, day, hour, expected in [
            (11, 20, 14, "2026-11-19"), (11, 20, 15, "2026-11-20"),
            (11, 23, 9, "2026-11-20"), (10, 7, 16, "2026-09-30"),
            (1, 2, 10, "2025-12-31"),
        ]:
            with self.subTest(expected=expected):
                now = datetime(2026, month, day, hour, tzinfo=probe.SHANGHAI)
                self.assertEqual(probe.latest_closed_session(now), date.fromisoformat(expected))

    def run_a(self, key, opener):
        now = datetime(2026, 11, 20, 17, tzinfo=probe.SHANGHAI)
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"HITHINK_FINANCE_API_KEY": key}), \
                patch.object(probe, "current_time", return_value=now), \
                patch.object(probe, "build_opener", return_value=opener), redirect_stdout(io.StringIO()):
            output = Path(tmp) / "probe.json"
            rc = probe.main(["--provider", "hithink", "--a", "600519.SH", "688525.SH", "--output", str(output)])
            self.assertEqual(rc, 2)
            return json.loads(output.read_text())

    def test_no_key_skips_each_symbol_before_network(self):
        data = self.run_a("", None)
        self.assertEqual(data["summary"]["not_run"], 2)
        self.assertTrue(all(row["status"] == "NOT_RUN_NO_CREDENTIALS" for row in data["results"]))

    def test_http_failures_and_network_error(self):
        from unittest.mock import Mock
        for status in (401, 403, 429):
            opener = Mock()
            opener.open.side_effect = HTTPError("https://example.invalid", status, "error", {},
                                               io.BytesIO(b'{"code":2003,"message":"test-key"}'))
            data = self.run_a("test-key", opener)
            for row in data["results"]:
                self.assertEqual((row["http_status"], row["api_code"]), (status, 2003))
                self.assertNotIn("test-key", str(row))
        opener = Mock()
        opener.open.side_effect = URLError(TimeoutError())
        data = self.run_a("test-key", opener)
        self.assertEqual(data["summary"]["fail"], 2)
        self.assertIn("NETWORK_UNAVAILABLE", data["results"][0]["reason"])

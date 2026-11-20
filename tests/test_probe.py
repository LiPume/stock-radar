"""Market API parser/quality tests with synthetic response fixtures only."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "market_probe.py"
spec = importlib.util.spec_from_file_location("market_probe", MODULE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def one_bar(close=100, stamp=1762905600000):
    return {"date_ms": stamp, "open_price": close, "high_price": close + 1,
            "low_price": close - 1, "close_price": close, "volume": 1000,
            "turnover": 100000}


class ParserTest(unittest.TestCase):
    def test_batch_group_missing_must_fail_individually(self):
        payload = {"code": 0, "data": {"interval": "1d", "adjust": "forward",
                                      "item": [{"thscode": "600519.SH", "item": [one_bar()]}]}}
        parsed, errors = probe.convert_hithink_payload(payload, ["600519.SH", "688525.SH"])
        self.assertEqual(len(parsed["600519.SH"]), 1)
        self.assertIn("688525.SH", errors)
        self.assertIn("SYMBOL_MISSING", errors["688525.SH"])

    def test_api_error_code(self):
        with self.assertRaisesRegex(ValueError, "API_ERROR"):
            probe.convert_hithink_payload({"code": 2001, "message": "invalid key"}, ["600519.SH"])

    def test_empty_group_is_not_a_success(self):
        payload = {"code": 0, "data": {"interval": "1d", "adjust": "forward",
                                      "item": [{"thscode": "600519.SH", "item": []}]}}
        parsed, errors = probe.convert_hithink_payload(payload, ["600519.SH"])
        self.assertNotIn("600519.SH", parsed)
        self.assertIn("EMPTY", errors["600519.SH"])

    def test_a_b_symbol_validation(self):
        self.assertEqual(probe.parse_market_code("900901.SH", "B"), "900901.SH")
        self.assertEqual(probe.parse_market_code("688525.SH", "A"), "688525.SH")
        with self.assertRaises(ValueError):
            probe.parse_market_code("900901.SH", "A")
        with self.assertRaises(ValueError):
            probe.parse_market_code("600519", "A")

    def test_bad_bars(self):
        good = [{"date": "2026-11-19", "open": 100, "high": 101,
                 "low": 99, "close": 100, "volume": 1000}]
        self.assertEqual(probe.check_bars("test", good, "DEMO", 2)["status"], "FAIL")
        bad = good + [{**good[0], "date": "2026-11-19"}]
        self.assertEqual(probe.check_bars("test", bad, "DEMO", 1)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()

"""Offline reference correctness tests. No external dependencies or network."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stock_radar.indicators.rsi import calculate_rsi_bundle, rsi_wilder
from stock_radar.rules.rsi import evaluate_rsi_v1


class RSITest(unittest.TestCase):
    def test_maturity(self):
        seq = rsi_wilder([100 + n for n in range(12)], 6)
        self.assertEqual(len(seq), 12)
        self.assertTrue(all(v is None for v in seq[:6]))
        self.assertEqual(seq[6], 100)

    def test_rising_falling_flat(self):
        self.assertEqual(rsi_wilder([1+i for i in range(40)], 6)[-1], 100)
        self.assertEqual(rsi_wilder([50-i for i in range(40)], 6)[-1], 0)
        self.assertEqual(rsi_wilder([100]*40, 6)[-1], 50)

    def test_invalid_inputs(self):
        for value in (float("nan"), 0, -1):
            with self.assertRaises(ValueError):
                rsi_wilder([100, value, 110], 2)
        with self.assertRaises(ValueError):
            rsi_wilder([100, 101], 0)

    def test_bundle(self):
        bundle = calculate_rsi_bundle([100 + i*.3 for i in range(60)])
        self.assertEqual(set(bundle), {"rsi6", "rsi12", "rsi24"})
        self.assertTrue(all(len(v) == 60 for v in bundle.values()))


class SignalTest(unittest.TestCase):
    def test_overbought_first_cross(self):
        r = evaluate_rsi_v1(79, 75, 82, 78)
        self.assertIn("RSI_OVERBOUGHT_CROSS", r["events"])
        self.assertEqual(r["status"], "WATCH")

    def test_stays_overbought_not_duplicate(self):
        r = evaluate_rsi_v1(82, 78, 83, 79)
        self.assertEqual(r["events"], [])
        self.assertEqual(r["status"], "WATCH")

    def test_high_death_cross(self):
        r = evaluate_rsi_v1(82, 76, 75, 77)
        self.assertIn("RSI_HIGH_DEATH_CROSS", r["events"])
        self.assertEqual(r["status"], "ALERT")

    def test_low_cross_not_high(self):
        r = evaluate_rsi_v1(62, 58, 53, 55)
        self.assertEqual(r["events"], [])
        self.assertEqual(r["status"], "NO_RULE_TRIGGERED")

    def test_no_yesterday_rsi_no_invented_event(self):
        r = evaluate_rsi_v1(None, None, 85, 82)
        self.assertEqual(r["events"], [])
        self.assertEqual(r["status"], "WATCH")

    def test_no_today_data(self):
        r = evaluate_rsi_v1(82, 75, None, None)
        self.assertEqual(r["events"], [])
        self.assertEqual(r["status"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()

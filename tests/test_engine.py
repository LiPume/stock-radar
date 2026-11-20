import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stock_radar.rules.engine import evaluate_history
from stock_radar.rules.rsi import evaluate_rsi_v1


class EngineTest(unittest.TestCase):
    def test_minimum_history(self):
        for bars in ([], [10] * 99):
            decision = evaluate_history(bars)
            self.assertEqual(decision["status"], "INSUFFICIENT_DATA")
            self.assertEqual(decision["events"], [])
        self.assertEqual(evaluate_history([10] * 100)["rsi24"], 50.0)

    def test_threshold_boundaries(self):
        self.assertEqual(evaluate_rsi_v1(80, 72, 81, 74)["events"], ["RSI_OVERBOUGHT_CROSS"])
        self.assertEqual(evaluate_rsi_v1(79, 70, 80, 71)["events"], [])
        self.assertEqual(evaluate_rsi_v1(70, 70, 68, 69)["events"], ["RSI_HIGH_DEATH_CROSS"])
        self.assertEqual(evaluate_rsi_v1(70, 69, 68, 68)["events"], [])

    def test_oversold_is_not_a_sell_signal(self):
        decision = evaluate_history(list(range(200, 100, -1)))
        self.assertEqual(decision["rsi6"], 0)
        self.assertEqual(decision["events"], [])

    def test_changed_thresholds_use_distinct_event_version(self):
        self.assertNotEqual(evaluate_history([1]*100)["strategy_version"],
                            evaluate_history([1]*100, high_level=75)["strategy_version"])

"""Synthetic daily bars exercise the probe without network or AKShare dependencies."""
from datetime import date, timedelta
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_probe import one_bar, probe


def daily(day, price=10):
    return {"date": day.isoformat(), "open": price, "high": price + 1,
            "low": price - 1, "close": price, "volume": 1000}


class QualityTest(unittest.TestCase):
    def test_three_completed_sessions_share_the_full_history_seed(self):
        start = date(2025, 11, 3)
        bars = []
        for offset in range(50):
            day = start + timedelta(days=offset)
            if day.weekday() < 5:
                bars.append(daily(day, 10 + offset % 7))
        end = date.fromisoformat(bars[-1]["date"])
        result = probe.check_bars("sample", bars, "test", 30, start_date=start, expected_date=end)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(n == 0 for n in result["null_counts"].values()))
        expected = probe.calculate_rsi_bundle([bar["close"] for bar in bars])
        self.assertEqual([r["bar_date"] for r in result["recent_rsi"]], [b["date"] for b in bars[-3:]])
        for name, series in expected.items():
            self.assertEqual([r[name] for r in result["recent_rsi"]], series[-3:])

    def test_stale_unclosed_and_out_of_window_sessions_fail_without_indicators(self):
        day = date(2025, 11, 18)
        for day_delta, earliest, error in [(1, day, "STALE"), (-1, day, "after last"),
                                          (0, day + timedelta(days=1), "before requested")]:
            with self.subTest(day_delta=day_delta, earliest=earliest):
                result = probe.check_bars("sample", [daily(day)], "test", 1,
                                          start_date=earliest,
                                          expected_date=day + timedelta(days=day_delta))
                self.assertEqual(result["status"], "FAIL")
                self.assertIn(error, result["reason"])
                self.assertNotIn("recent_rsi", result)

    def test_numeric_quality_failure_preserves_null_counts(self):
        for field, value, null_count in [("open", None, 1), ("close", float("nan"), 1),
                                         ("volume", None, 1), ("volume", -1, 0),
                                         ("volume", float("inf"), 0), ("high", float("inf"), 0),
                                         ("high", 8, 0)]:
            with self.subTest(field=field, value=value):
                row = daily(date(2025, 11, 18))
                row[field] = value
                result = probe.check_bars("sample", [row], "test", 1)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["null_counts"][field], null_count)
                self.assertNotIn("recent_rsi", result)

    def test_interval_and_adjust_must_match_the_request(self):
        for interval, adjust in [("1w", "forward"), ("1d", "none"), ("1d", None)]:
            with self.subTest(interval=interval, adjust=adjust):
                data = {"interval": interval, "adjust": adjust, "item": []}
                with self.assertRaisesRegex(ValueError, "1d/forward"):
                    probe.convert_hithink_payload({"code": 0, "data": data}, ["600519.SH"])

    def test_nulls_survive_hithink_normalization(self):
        bar = one_bar()
        bar["close_price"] = None
        bar["date_ms"] = None
        data = {"interval": "1d", "adjust": "forward",
                "item": [{"thscode": "600519.SH", "item": [bar]}]}
        parsed, errors = probe.convert_hithink_payload({"code": 0, "data": data}, ["600519.SH"])
        self.assertEqual(errors, {})
        result = probe.check_bars("600519.SH", parsed["600519.SH"], "test", 1)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["null_counts"]["close"], 1)
        self.assertEqual(result["null_counts"]["date"], 1)

    def test_akshare_adapter_preserves_disordered_dates(self):
        start, end = date(2025, 11, 17), date(2025, 11, 18)
        frame = Mock(empty=False, columns=list(probe.BAR_FIELDS))
        frame.astype.return_value.where.return_value.to_dict.return_value = [daily(end), daily(start)]
        ak = Mock()
        ak.stock_zh_b_daily.return_value = frame
        with patch.dict(sys.modules, {"akshare": ak}):
            rows = probe.fetch_akshare_b("900901.SH", start, end)
        ak.stock_zh_b_daily.assert_called_once_with(symbol="sh900901", start_date="20251117",
                                                    end_date="20251118", adjust="qfq")
        result = probe.check_bars("900901.SH", rows, "test", 1)
        self.assertIn("unsorted", result["reason"])

    def test_akshare_errors_are_reproducible_and_redacted(self):
        day = date(2025, 11, 18)
        for failure, expected in [(TimeoutError("sensitive-url"), "NETWORK_UNAVAILABLE"),
                                  (ValueError("sensitive-url"), "UPSTREAM_ERROR")]:
            ak = Mock()
            ak.stock_zh_b_daily.side_effect = failure
            with self.subTest(expected=expected), patch.dict(sys.modules, {"akshare": ak}):
                with self.assertRaisesRegex(RuntimeError, expected) as caught:
                    probe.fetch_akshare_b("900901.SH", day, day)
                self.assertNotIn("sensitive-url", str(caught.exception))

    def test_akshare_empty_and_missing_columns(self):
        day = date(2025, 11, 18)
        for frame, reason in [(Mock(empty=True), "EMPTY"),
                              (Mock(empty=False, columns=["date"]), "SCHEMA_CHANGED")]:
            ak = Mock()
            ak.stock_zh_b_daily.return_value = frame
            with self.subTest(reason=reason), patch.dict(sys.modules, {"akshare": ak}):
                with self.assertRaisesRegex(RuntimeError, reason):
                    probe.fetch_akshare_b("900901.SH", day, day)

    def test_new_akshare_entry_point_uses_daily_frequency(self):
        day = date(2025, 11, 18)
        frame = Mock(empty=False, columns=list(probe.BAR_FIELDS))
        frame.astype.return_value.where.return_value.to_dict.return_value = [daily(day)]
        ak = SimpleNamespace(stock_zh_b_hist_sina=Mock(return_value=frame), __version__="test")
        metadata = {}
        with patch.dict(sys.modules, {"akshare": ak}):
            rows = probe.fetch_akshare_b("900901.SH", day, day, metadata=metadata)
        ak.stock_zh_b_hist_sina.assert_called_once_with(symbol="sh900901", period="daily",
            start_date="20251118", end_date="20251118", adjust="qfq")
        self.assertEqual(metadata["provider_function"], "stock_zh_b_hist_sina")
        self.assertEqual(rows[0]["date"], "2025-11-18")

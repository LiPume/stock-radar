from datetime import date
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from scripts.monitor_usage import usage_report


class MonitorUsageTest(unittest.TestCase):
    def test_report_reads_live_wal_and_only_prints_daily_counts(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "monitor #1.db"
            connection = sqlite3.connect(path)
            self.addCleanup(connection.close)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE market_usage(at,provider,kind,status);
                CREATE TABLE notification_deliveries(created_at,status,attempts,kind,body);
            """)
            connection.executemany("INSERT INTO market_usage VALUES(?,?,?,?)", [
                ("2026-11-13T23:59:59+08:00","hithink","HTTP","OK"),
                ("2026-11-14T00:00:00+08:00","hithink","HTTP","OK"),
                ("2026-11-20T14:30:00+08:00","akshare-b","HTTP","RATE_LIMIT"),
                ("2026-11-20T14:30:00+08:00","akshare-b","HTTP","PENDING"),
                ("2026-11-20T15:45:00+08:00","akshare-b","CACHE","OK"),
                ("2026-11-20T15:45:00+08:00","akshare-b","QUERY","OK"),
                ("2026-11-21T00:00:00+08:00","akshare-b","HTTP","OK"),
            ])
            connection.executemany("INSERT INTO notification_deliveries VALUES(?,?,?,?,?)", [
                ("2026-11-20T14:30:00+08:00","SENT",2,"brief","sensitive material"),
                ("2026-11-20T15:45:00+08:00","SKIPPED",0,"watch","private notes"),
                ("2026-11-20T16:15:00+08:00","UNKNOWN",1,"alert","personal information"),
            ])
            connection.commit()
            version = connection.execute("PRAGMA data_version").fetchone()
            report = usage_report(path, 7, date(2026, 11, 20))
            self.assertEqual(connection.execute("PRAGMA data_version").fetchone(), version)
            self.assertIn("| 2026-11-14 | hithink | 1 | 1 | 0 | 0 | 0 | 0 |", report)
            self.assertIn("| 2026-11-20 | akshare-b | 2 | 0 | 1 | 1 | 1 | 1 |", report)
            self.assertIn("| 2026-11-20 | 3 | 3 | 1 | 0 | 1 | 0 | 1 | 1 |", report)
            self.assertIn("| 合计 | akshare-b | 2 | 0 | 1 | 1 | 1 | 1 |", report)
            self.assertIn("| 合计 | 3 | 3 | 1 | 0 | 1 | 0 | 1 | 1 |", report)
            self.assertIn("| 2026-11-15 | hithink | 0 | 0 | 0 | 0 | 0 | 0 |", report)
            for excluded in ("2026-11-13", "2026-11-21", "sensitive", "private", "personal"):
                self.assertNotIn(excluded, report)
            connection.close()

    def test_missing_file_is_not_created_and_days_are_validated(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "missing.db"
            with self.assertRaises(FileNotFoundError):
                usage_report(path, 7, date(2026, 11, 20))
            for invalid in (0, 367):
                with self.assertRaises(ValueError):
                    usage_report(path, invalid, date(2026, 11, 20))
            self.assertFalse(path.exists())

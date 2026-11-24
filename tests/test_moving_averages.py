from datetime import date, datetime, time, timedelta
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from backend.app.auth import Auth
from backend.app.db import CheckRun, DailyBar, Holding, IntradayReading
from backend.app.main import create_app
from backend.app.moving_averages import latest_averages, rolling_averages
from scripts.market_probe import SHANGHAI, is_trading_day
from test_v11_dashboard import IdleScanner


class MovingAverageTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(TemporaryDirectory())
        app = create_app("sqlite:///" + tmp + "/test.db", "fixture", IdleScanner)
        self.client = self.enterContext(TestClient(app))
        app.state.auth.tokens[Auth.digest("test")] = float("inf")
        self.headers = {"Authorization":"Bearer test"}
        self.db = app.state.db
        day = date(2026, 10, 9)
        self.dates = []
        while len(self.dates) < 28:
            if is_trading_day(day):
                self.dates.append(day.isoformat())
            day += timedelta(days=1)
        self.enterContext(patch("backend.app.main.current_time",
            return_value=datetime.combine(date.fromisoformat(self.dates[-1]), time(16), SHANGHAI)))

    @staticmethod
    def point(symbol, day, price, provider="hithink", adjust="forward"):
        return DailyBar(symbol=symbol, bar_date=day, provider=provider, adjust=adjust,
                        open=price, high=price, low=price, close=price, volume=200, fetched_at=day)

    def load_prices(self, session, size, symbol="600000.SH", market="A", multiplier=1):
        holding = Holding(symbol=symbol, market=market, bar_date=self.dates[size - 1],
                          data_status="OK", status="NO_RULE_TRIGGERED")
        session.add(holding)
        provider, adjust = ("hithink", "forward") if market == "A" else ("akshare-b", "qfq")
        for index, day in enumerate(self.dates[:size], 1):
            session.add(self.point(symbol, day, index * multiplier, provider, adjust))
        session.flush()
        return holding

    def test_first_full_windows_and_price_basis_change(self):
        samples = [("hithink", "forward", i) for i in range(1, 21)]
        samples += [("other", "forward", 100), ("other", "qfq", 50)]
        result = list(rolling_averages(samples))
        self.assertEqual(result[3], {"ma5":None, "ma10":None, "ma20":None})
        self.assertEqual(result[4], {"ma5":3, "ma10":None, "ma20":None})
        self.assertEqual(result[9], {"ma5":8, "ma10":5.5, "ma20":None})
        self.assertEqual(result[19], {"ma5":18, "ma10":15.5, "ma20":10.5})
        self.assertTrue(all(v is None for row in result[20:] for v in row.values()))

    def test_chart_warmup_one_query_and_arithmetic_isolated_per_stock(self):
        with self.db.sessions.begin() as s:
            self.load_prices(s, 27)
            self.load_prices(s, 27, symbol="900901.SH", market="B", multiplier=2)
            s.add(self.point("600000.SH", "2030-01-01", 999))
            for day in self.dates[:27]:
                s.add(self.point("600000.SH", day, 999, "hithink", "raw"))
                s.add(self.point("600000.SH", day, 400, "other", "forward"))
                s.add(self.point("600000.SH", day, 800, "hithink", "qfq"))
        daily_queries = []
        def count_query(conn, cursor, stmt, params, context, many):
            if "FROM daily_bars" in stmt:
                daily_queries.append(stmt)
        engine = self.db.engine
        event.listen(engine, "before_cursor_execute", count_query)
        try:
            bars = self.client.get("/api/v1/stocks/600000.SH/bars?days=3",headers=self.headers).json()["data"]
        finally:
            event.remove(engine, "before_cursor_execute", count_query)
        self.assertEqual(len(daily_queries), 1)
        self.assertEqual([b["ma20"] for b in bars], [15.5, 16.5, 17.5])
        self.assertEqual([b["close"] for b in bars], [25,26,27])
        latest = self.client.get("/api/v1/stocks/900901.SH/bars?days=1",headers=self.headers).json()["data"]
        self.assertEqual([(b["ma5"], b["ma10"], b["ma20"]) for b in latest], [(50,45,35)])

    def test_provisional_replaces_same_day_and_never_modifies_history(self):
        with self.db.sessions.begin() as s:
            self.load_prices(s, 27)
            day = self.dates[19]
            formal = latest_averages(s, "600000.SH", "A", day=day)
            self.assertEqual(formal, {"ma5":18, "ma10":15.5, "ma20":10.5})
            for price in (100, 80):
                provisional = latest_averages(s, "600000.SH", "A", day=day, provisional_price=price)
                self.assertEqual(provisional["ma20"], (sum(range(1,20))+price)/20)
                self.assertEqual(provisional["ma5"], (sum(range(16,20))+price)/5)
            latest = latest_averages(s, "600000.SH", "A", day=self.dates[0] , provisional_price=100)
            self.assertTrue(all(v is None for v in latest.values()))
            self.assertEqual(s.scalar(select(func.count(DailyBar.id))), 27)
            self.assertEqual(s.scalar(select(DailyBar.close).where(DailyBar.bar_date == day)), 20)

    def test_holdings_agree_with_the_displayed_price_context(self):
        with self.db.sessions.begin() as s:
            self.load_prices(s, 20)
            self.load_prices(s, 10, symbol="900901.SH", market="B", multiplier=2)
        today = date.fromisoformat(self.dates[20])
        clock = datetime.combine(today, time(14, 30), SHANGHAI)
        with patch.dict("os.environ", {"INTRADAY_VALIDATED": "true"}), \
             patch("backend.app.main.current_time", return_value=clock):
            def holdings():
                items = self.client.get("/api/v1/holdings", headers=self.headers).json()["data"]
                return {i["symbol"]:i for i in items}
            a = holdings()["600000.SH"]
            self.assertEqual((a["price_context"], a["ma20"]), ("CLOSE",10.5))
            self.assertEqual(holdings()["900901.SH"]["ma10"],11)
            with self.db.sessions.begin() as s:
                h = s.scalar(select(Holding).where(Holding.symbol == "600000.SH"))
                run = CheckRun(trigger_type="intraday")
                s.add(run)
                s.flush()
                s.add(IntradayReading(run_id=run.id, holding_id=h.id, symbol=h.symbol, trade_date=today.isoformat(),
                    observed_at=clock.isoformat(), quote_time=clock.isoformat(), provider="hithink", status="WATCH",
                    data_status="OK", currency="CNY", price=41, snapshot={"adjustment_verified":True}))
            a = holdings()["600000.SH"]
            self.assertEqual((a["price_context"], a["price"], a["ma20"]), ("INTRADAY", 41, (sum(range(2,21))+41)/20))
            with self.db.sessions.begin() as s:
                row = s.scalar(select(IntradayReading))
                row.data_status, row.status, row.reason = "DATA_UNAVAILABLE", "DATA_UNAVAILABLE", "交易所时标缺失"
            a = holdings()["600000.SH"]
            self.assertEqual((a["price_context"], a["quote_time"], a["ma20"]), ("LAST_CLOSE", self.dates[19], 10.5))

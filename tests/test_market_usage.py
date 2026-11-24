"""Offline transport accounting and cautious source validation; no production data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

from backend.app.db import DB, Holding, Setting
from backend.app.market_usage import MarketBlocked, Meter, usage_summary
from backend.app import providers, snapshot_provider as snapshots
from scripts.market_probe import ProbeFailure, SHANGHAI

class MarketUsageTest(unittest.TestCase):
    def setUp(self):
        path = Path(self.enterContext(TemporaryDirectory())) / "ledger.db"
        self.db = DB("sqlite:///" + str(path))
        self.db.migrate()
        self.now = datetime(2026,11,20,10,0,tzinfo=SHANGHAI)
        self.enterContext(patch.dict(os.environ, {"HITHINK_FINANCE_API_KEY":"fixture",
            "HITHINK_VERIFIED_ROW_TIME_FIELD":""}))
        self.addCleanup(self.db.engine.dispose)

    def meter(self, priority="normal", provider="hithink", offset=0):
        return Meter(self.db,provider,self.now+timedelta(minutes=offset),priority,"snapshot")

    def test_concurrent_quota_and_reserved_close_brief(self):
        with self.db.sessions.begin() as s:
            s.add(Holding(symbol="600519.SH",market="A"))
            s.get(Setting,1).api_daily_limit=4
        def run(_):
            try:
                self.meter().request(lambda:1)
                return True
            except MarketBlocked:
                return False
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(run, range(8))),2)
        self.meter("brief").request(lambda:1)
        self.meter("close").request(lambda:1)
        with self.assertRaises(MarketBlocked):
            self.meter("close").request(lambda:1)

    def test_rate_limit_is_bounded_and_auth_stops_across_reopen(self):
        def fail():
            raise ProbeFailure("RATE_LIMIT",api_code=4001,http_status=200)
        with self.assertRaises(ProbeFailure):
            self.meter().request(fail)
        with self.assertRaises(MarketBlocked):
            self.meter().request(lambda:True)
        self.meter(offset=2).request(lambda: True)
        def deny():
            raise ProbeFailure("no permission",api_code=2003)
        with self.assertRaises(ProbeFailure):
            self.meter(offset=2).request(deny)
        db=DB(self.db.engine.url.render_as_string())
        with self.assertRaises(MarketBlocked):
            Meter(db,"hithink",self.now+timedelta(hours=1),"close","close").request(lambda:True)
        next_day=self.now+timedelta(days=1)
        Meter(db,"hithink",next_day,"normal","snapshot").request(lambda:True)
        stats=usage_summary(db,next_day)["hithink"]
        self.assertEqual(stats["month"]["rate_limited"],1)
        self.assertEqual(stats["month"]["failures"],2)
        self.assertEqual(stats["today"]["http_requests"],1)
        db.engine.dispose()

    def test_batch_cache_filter_and_missing_timestamp_even_on_singleton(self):
        symbols=[("600519.SH","A"),("000001.SZ","A")]
        data={"timestamp":int(self.now.timestamp()*1000),
              "item":[{"thscode":s,"last_price":20,"prev_price":19} for s,m in symbols]}
        def response(path, params):
            if path.endswith("adjustment-factors"):
                return {"thscode":params["thscode"],"item":[{"ex_date_ms":int((self.now-timedelta(days=3)).timestamp()*1000)}]}
            return data
        with patch.object(snapshots,"fetch_json",side_effect=response) as get:
            first=snapshots.get_snapshots(self.db,symbols,self.now,"normal")
            second=snapshots.get_snapshots(self.db,symbols,self.now+timedelta(minutes=4),"normal")
            self.assertEqual(first,second)
            self.assertEqual(sum(call.args[0].endswith("/snapshot") for call in get.call_args_list),1)
            self.assertTrue(all(q["adjustment_verified"] and q["quote_time"] is None for q in second.values()))
            snapshots.get_snapshots(self.db,symbols,self.now+timedelta(minutes=5),"normal")
            self.assertEqual(sum(call.args[0].endswith("/snapshot") for call in get.call_args_list),2)
        one=snapshots.unpack_a({**data,"item":data['item'][:1]},[symbols[0][0]])[symbols[0][0]]
        self.assertIsNone(one["quote_time"])
        self.assertFalse(one["adjustment_verified"])
        self.assertEqual(usage_summary(self.db,self.now)["hithink"]["today"]["cache_hits"],1)

    def test_dated_adjustment_check_rejects_event_and_caches_evidence(self):
        symbol="600519.SH"
        stamp=int(self.now.timestamp()*1000)
        item={"thscode":symbol,"trade_ms":stamp,"last_price":20}
        for event_today in (True,False):
            when=self.now if event_today else self.now-timedelta(days=3)
            now=self.now+timedelta(days=0 if event_today else 1)
            item["trade_ms"]=int(now.timestamp()*1000)
            with patch.dict(os.environ,{"HITHINK_VERIFIED_ROW_TIME_FIELD":"trade_ms"}), patch.object(
                snapshots,"fetch_json",side_effect=[{"item":[item]}, {"thscode":symbol,
                    "item":[{"ex_date_ms":int(when.timestamp()*1000)}]}]):
                quote=snapshots.get_snapshots(self.db,[(symbol,"A")],now,"normal")[symbol]
                self.assertEqual(quote["adjustment_verified"],not event_today)

    def test_sina_one_market_query_and_no_dateless_rsi(self):
        stocks=[("900901.SH","B"),("200001.SZ","B")]
        result={"selected":{"900901.SH":{"名称":"测试B","昨收":1.2,"最新价":1.3}},
                "source_records":72}
        with patch.object(snapshots,"run_metered_b",return_value=result) as sdk:
            quotes=snapshots.get_snapshots(self.db,stocks,self.now,"normal",probe_b=True)
            self.assertEqual(sdk.call_count,1)
            self.assertIsNone(quotes["900901.SH"]["quote_time"])
            self.assertEqual(quotes["900901.SH"]["currency"],"USD")
            self.assertIn("error",quotes["200001.SZ"])
        self.assertEqual(usage_summary(self.db,self.now)["akshare-b"]["today"]["records"],72)

    def test_close_only_b_never_spends_a_request_on_undated_snapshot(self):
        with patch.object(snapshots, "run_metered_b") as worker:
            rows = snapshots.get_snapshots(self.db,[("900901.SH","B")],self.now,"brief")
            self.assertIn("收盘监控",rows["900901.SH"]["error"])
            worker.assert_not_called()

    def test_auth_error_in_daily_history_counts_error_and_suppresses_retry(self):
        args=(self.db,[("600519.SH","A")],self.now.date(),self.now.date(),self.now,"close")
        with patch.object(providers.probe,"fetch_hithink",return_value=({"code":2003},{})) as remote:
            self.assertIn("2003",list(providers.get_history(*args))[0][2])
            self.assertIn("SOURCE_PAUSED",list(providers.get_history(*args))[0][2])
            self.assertEqual(remote.call_count,1)

    def test_akshare_physical_requests_count_and_stop_at_cap(self):
        try:
            from urllib3.connectionpool import HTTPConnectionPool
            import requests
        except ImportError:
            self.skipTest("Optional probe dependencies are absent")
        from backend.app.sdk_transport import metered_sdk
        from types import SimpleNamespace
        with self.db.sessions.begin() as s:
            s.get(Setting,1).api_daily_limit=2
        meter=self.meter("close","akshare-b")
        with patch.object(HTTPConnectionPool,"_make_request",return_value=SimpleNamespace(status=200)) as http:
            with self.assertRaises(MarketBlocked), metered_sdk(meter):
                pool=HTTPConnectionPool("test.invalid")
                for _ in range(3):
                    pool._make_request(None,"GET","/")
            self.assertEqual(http.call_count,2)
        self.assertEqual(usage_summary(self.db,self.now)["akshare-b"]["today"]["http_requests"],2)

class MarketCertificationTest(unittest.TestCase):
    def test_weekend_capture_skips_database_credentials_and_network(self):
        from scripts import verify_intraday_source as verify
        now = datetime(2026,11,21,10,0,tzinfo=SHANGHAI)
        with patch.object(verify, "datetime", wraps=datetime) as clock, patch.object(verify, "get_snapshots") as fetch:
            clock.now.return_value = now
            result = verify.capture(Path("does-not-exist"), Path("does-not-exist"))
        self.assertEqual(result["status"], "AWAITING_SESSION")
        fetch.assert_not_called()

    def test_reference_accepts_nearby_terminal_sample_and_requires_all_three_rsi(self):
        from scripts.verify_intraday_source import compare
        sb = "600519.SH"
        sample = dict(quote_time="2026-11-20T10:00:00+08:00",price=80.,rsi6=50.,rsi12=60.,rsi24=70.)
        report={"samples":{sb:sample}}
        terminal={**sample,"source":"独立行情终端"}
        self.assertEqual(compare(report,{sb:terminal})["status"],"MATCHED")
        for changed in ({"rsi24":70.6},{"price":80.5},{"source":""},{"quote_time":"2026-11-20T10:05:01+08:00"},
                        {"rsi6":float("nan")},{"quote_time":"2026-11-20T10:00:00"}):
            self.assertEqual(compare(report,{sb:{**terminal,**changed}})["status"],"INCOMPLETE")
        report["samples"][sb]["error"]="除权未核实"
        self.assertEqual(compare(report,{sb:terminal})["status"],"INCOMPLETE")
        self.assertEqual(compare({"samples":{}},{})["status"],"INCOMPLETE")

    def test_reference_capture_isolated_persistent_ledger(self):
        from scripts.verify_intraday_source import inspection_db
        with TemporaryDirectory() as d:
            backup=Path(d)/"ref.db"
            backup.touch()
            dpath=Path(d)/"isolated"
            for _ in range(2):
                db=inspection_db(dpath,[("600519.SH","A")],backup)
                with db.sessions() as session:
                    self.assertEqual(session.query(Holding).count(),1)
                db.engine.dispose()
            with self.assertRaises(ValueError):
                inspection_db(dpath,[],dpath/"source-validation.sqlite3")
            self.assertEqual(backup.stat().st_size,0)

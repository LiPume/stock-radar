"""HiThink batch snapshots and the bounded Sina B-share snapshot worker."""
from datetime import datetime, timedelta
from http.client import HTTPException
from time import monotonic
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .market_usage import Meter
from .providers import failure_reason, retry_network, run_metered_b
from .securities import currency_for, verified_name
from scripts.market_probe import NoKeyRedirect, ProbeFailure, SHANGHAI

CACHE_AGE = timedelta(minutes=5)
SNAPSHOT_BATCH = 100  # local URL-size bound, not the provider's documented limit


def fetch_json(path, params):
    key = os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
    base = os.getenv("HITHINK_FINANCE_BASE_URL", "https://fuyao.aicubes.cn").rstrip("/")
    if not key or not key.isascii() or not key.isprintable() or not base.startswith("https://"):
        raise ValueError("AUTH: 行情凭据或后端地址无效")
    req = Request(f"{base}{path}?{urlencode(params)}",headers={"X-api-key":key,"Accept":"application/json"})
    try:
        with build_opener(NoKeyRedirect()).open(req,timeout=10) as response:
            data = json.load(response)
    except HTTPError as exc:
        exc.close()
        raise ProbeFailure(f"HTTP_ERROR: status={exc.code}",http_status=exc.code) from None
    except (OSError,HTTPException):
        raise RuntimeError("NETWORK_UNAVAILABLE: 行情服务连接失败") from None
    code = data.get("code") if isinstance(data,dict) else None
    if type(code) is not int or not isinstance(data.get("data"),dict) and code==0:
        raise ValueError("SCHEMA_CHANGED: 行情应答缺少业务码或数据")
    if code:
        raise ProbeFailure(f"API_ERROR code={code}",api_code=code)
    return data["data"]


def quote_stamp(value):
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value/1000,SHANGHAI).isoformat()
    except (OSError,OverflowError,ValueError):
        return None


def unpack_a(data,symbols):
    items = data.get("item")
    if not isinstance(items,list):
        raise ValueError("SCHEMA_CHANGED: A 股快照缺少 item")
    batch_time = quote_stamp(data.get("timestamp"))
    result={}
    for item in items:
        if not isinstance(item,dict):
            continue
        symbol=item.get("thscode")
        if symbol not in symbols:
            continue
        if symbol in result:
            result[symbol]={"error":"行情批次包含重复证券"}
            continue
        # Even on a Saturday the live API supplied a batch timestamp close to wall
        # time. Do not interpret the envelope readiness time as any stock's trade time.
        quote_time = None
        # Optional per-row epoch-ms field: configure ONLY after the vendor documents
        # trade-time semantics and live certification verifies it (not a receipt clock).
        field=os.getenv("HITHINK_VERIFIED_ROW_TIME_FIELD","")
        if field and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}",field):
            quote_time = quote_stamp(item.get(field))

        result[symbol]=dict(provider="hithink", name=verified_name(symbol), currency="CNY",
            price=item.get("last_price"), prev_close=item.get("prev_price"), open=item.get("open_price"),
            high=item.get("high_price"), low=item.get("low_price"), change_pct=item.get("price_change_ratio_pct"),
            quote_time=quote_time, batch_time=batch_time, adjustment_verified=False)
        if quote_time is None:
            result[symbol]["error"]="行情源未提供可核实的逐股交易时间"
    return result


def adjustment_verified(meter,symbol):
    # Fetch a positive, symbol-specific event history. NO_DATA or malformed history never
    # establishes absence of corporate actions. Revalidate daily; the engine also checks
    # the cached previous trade date, raw reference close and recalculated RSI baseline.
    day=meter.now.date().isoformat()
    key=f"factors:{symbol}:{day}"
    cached=meter.read_cache(key,timedelta(days=1),1)
    if cached is not None:
        return cached
    meter.log("QUERY",1)
    payload=retry_network(meter,lambda:fetch_json("/api/a-share/corporate-actions/adjustment-factors",
                                                  {"thscode":symbol,"to":day}))
    rows=payload.get("item")
    if payload.get("thscode")!=symbol or not isinstance(rows,list) or not rows:
        return False
    dates=[quote_stamp(r.get("ex_date_ms")) if isinstance(r,dict) else None for r in rows]
    meter.log("RESULT",records=len(rows))
    verified=all(dt is not None and dt[:10]<day for dt in dates)
    meter.save_cache(key,verified)
    return verified


def get_snapshots(db,stocks,now,priority, *, probe_b=False):
    """Return quotes keyed by code; the engine only evaluates verified prices and times."""
    output={}
    for market,provider in (("A","hithink"),("B","akshare-b")):
        symbols=sorted({s for s,m in stocks if m==market})
        if not symbols:
            continue
        meter=Meter(db,provider,now,priority,"snapshot")
        if market == "B" and not probe_b:
            # Both verified candidates lack a usable dated trade clock. Keep quota
            # for official closing history until a new B adapter can supply one.
            for symbol in symbols:
                output[symbol] = {"provider": provider, "error": "B 股快照尚无可核实的逐股时间，仅作收盘监控"}
            continue
        size=SNAPSHOT_BATCH if market=="A" else len(symbols)
        for i in range(0,len(symbols),size):
            group=symbols[i:i+size]
            meter.log("QUERY",len(group))
            cache_key=f"snapshot:{provider}:{','.join(group)}"
            rows=meter.read_cache(cache_key,CACHE_AGE,len(group))
            if rows is None:
                try:
                    if market=="A":
                        if not os.getenv("HITHINK_FINANCE_API_KEY","").strip():
                            raise RuntimeError("AUTH: 后端未配置行情凭据")
                        started = monotonic()
                        data=retry_network(meter,lambda:fetch_json("/api/a-share/prices/snapshot",{"thscodes":",".join(group)}))
                        rows=unpack_a(data,group)
                        meter.log("RESULT",records=len(data["item"]))
                        for symbol,row in rows.items():
                            stamp=row.get("quote_time")
                            received = meter.now + timedelta(seconds=monotonic() - started)
                            if stamp and timedelta(0)<=received-datetime.fromisoformat(stamp)<=CACHE_AGE:
                                try:
                                    row["adjustment_verified"]=adjustment_verified(meter,symbol)
                                except (RuntimeError, ValueError, OSError) as exc:
                                    row["error"] = "复权口径未核实 · " + failure_reason(exc)
                    else:
                        data=run_metered_b(meter,group)
                        meter.log("RESULT",records=data["source_records"])
                        rows={}
                        for symbol,row in data["selected"].items():
                            rows[symbol]=dict(provider=provider,name=row.get("名称"),currency=currency_for(symbol,"B"),
                                price=row.get("最新价"),prev_close=row.get("昨收"),open=row.get("今开"),
                                high=row.get("最高"),low=row.get("最低"),change_pct=row.get("涨跌幅"),
                                quote_time=None,adjustment_verified=False,error="B 股快照缺少交易日期与逐股时间，仅作收盘监控")
                    meter.save_cache(cache_key,rows)
                except (RuntimeError,ValueError,OSError) as exc:
                    rows={symbol:{"error":failure_reason(exc)} for symbol in group}
            for symbol in group:
                output[symbol]=rows.get(symbol, {"error":"行情快照未返回该股"})
    return output

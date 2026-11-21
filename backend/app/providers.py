"""Real daily history adapters. Keys and HTTP exception bodies stay in the backend."""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import redirect_stdout
from datetime import date
import io
import json
import os
import subprocess
import sys
from time import sleep

from scripts import market_probe as probe


def failure_reason(error: Exception) -> str:
    # market_probe emits sanitized categories. Never echo arbitrary upstream bodies.
    message = str(error)
    for prefix in ("REQUEST_BUDGET:", "SOURCE_PAUSED:", "RATE_LIMIT:", "AUTH", "API_ERROR code=", "HTTP_ERROR", "NETWORK_UNAVAILABLE",
                   "EMPTY", "SCHEMA_CHANGED", "DEPENDENCY_MISSING", "UPSTREAM_ERROR"):
        if message.startswith(prefix):
            return message[:180]
    return f"UPSTREAM_ERROR: {type(error).__name__}"


def b_history(symbol: str, start: date, end: date) -> list[dict]:
    # The SDK has several blocking requests with no deadline. Bound the entire fetch.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.app.providers", symbol, start.isoformat(), end.isoformat()],
            capture_output=True, timeout=45, check=True, text=True,
        )
        reply = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("NETWORK_UNAVAILABLE: AKShare timed out") from None
    except (ValueError, subprocess.CalledProcessError):
        raise RuntimeError("UPSTREAM_ERROR: AKShare worker failed") from None
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["bars"]


def fetch_history(stocks: Sequence[tuple[str, str]], start: date, end: date
                  ) -> Iterator[tuple[str, list[dict], str | None]]:
    """Yield a result for each requested (symbol, market), including missing A groups."""
    a = [symbol for symbol, market in stocks if market == "A"]
    b = [symbol for symbol, market in stocks if market == "B"]
    for i in range(0, len(a), 10):
        batch = a[i:i + 10]
        try:
            payload, _metadata = probe.fetch_hithink(batch, start, end, os.getenv("HITHINK_FINANCE_API_KEY", "").strip())
            data, errors = probe.convert_hithink_payload(payload, batch)
        except (RuntimeError, ValueError, OSError) as exc:
            data, errors = {}, dict.fromkeys(batch, failure_reason(exc))
        for symbol in batch:
            yield symbol, data.get(symbol, []), errors.get(symbol)
    for i, symbol in enumerate(b):
        if i:
            sleep(1)
        try:
            rows = b_history(symbol, start, end)
        except (RuntimeError, ValueError, OSError) as exc:
            yield symbol, [], failure_reason(exc)
        else:
            yield symbol, rows, None


def run_metered_b(meter, symbols, **params):
    job = dict(database=meter.db.engine.url.render_as_string(hide_password=False),
               symbols=symbols, now=meter.now.isoformat(), operation=meter.operation,
               priority=meter.priority, **params)
    try:
        completed = subprocess.run([sys.executable, "-m", "backend.app.market_worker"],
            input=json.dumps(job), text=True, capture_output=True, timeout=45, check=True)
        reply = json.loads(completed.stdout)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
        raise RuntimeError("NETWORK_UNAVAILABLE: B 股行情工作进程中断") from None
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["result"]


def get_history(db, stocks, start, end, now, priority):
    """Production daily path: shared ledger, one physical budget across operations."""
    from .market_usage import Meter
    a = [symbol for symbol, market in stocks if market == "A"]
    key = os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
    for i in range(0,len(a),10):
        batch=a[i:i+10]
        meter=Meter(db,"hithink",now,priority,"close")
        meter.log("QUERY",len(batch))
        try:
            if not key:
                raise RuntimeError("AUTH: 缺少行情凭据")
            def get_batch():
                payload, _ = probe.fetch_hithink(batch,start,end,key)
                if payload.get("code") in (2001,2003,4001):
                    code = payload["code"]
                    raise probe.ProbeFailure(f"API_ERROR code={code}",api_code=code)
                return probe.convert_hithink_payload(payload,batch)
            rows, errors = retry_network(meter,get_batch)
            meter.log("RESULT",records=sum(len(bars) for bars in rows.values()))
        except (RuntimeError, ValueError, OSError) as exc:
            rows, errors = {}, dict.fromkeys(batch,failure_reason(exc))
        for symbol in batch:
            yield symbol,rows.get(symbol,[]),errors.get(symbol)
    for symbol,market in stocks:
        if market != "B":
            continue
        meter=Meter(db,"akshare-b",now,priority,"close")
        meter.log("QUERY",1)
        try:
            bars=run_metered_b(meter,[symbol],start=start.isoformat(),end=end.isoformat())
            meter.log("RESULT",records=len(bars))
            yield symbol,bars,None
        except (RuntimeError,ValueError,OSError) as exc:
            yield symbol,[],failure_reason(exc)


def retry_network(meter, func):
    for attempt in range(2):
        try:
            return meter.request(func)
        except (RuntimeError, OSError) as exc:
            if attempt or not (str(exc).startswith("NETWORK_UNAVAILABLE") or isinstance(exc, TimeoutError)):
                raise
            sleep(1)


if __name__ == "__main__":
    try:
        code = probe.parse_market_code(sys.argv[1], "B")
        with redirect_stdout(io.StringIO()):
            rows = probe.fetch_akshare_b(code, date.fromisoformat(sys.argv[2]), date.fromisoformat(sys.argv[3]))
        print(json.dumps({"bars": rows}))
    except Exception as exc:
        print(json.dumps({"error": failure_reason(exc)}))

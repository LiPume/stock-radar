#!/usr/bin/env python3
"""Stock Radar REST/AKShare data feasibility probe.

- No secrets stored or printed.
- No network in demo mode.
- Real live probes require explicit `--provider hithink|akshare-b|all`.
- This is NOT production monitoring, NOT a broker or trading interface.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from stock_radar.indicators.rsi import calculate_rsi_bundle

SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_OUTPUT = ROOT / "reports" / "probe_result.json"
B_SYMBOL_RE = re.compile(r"^(900\d{3})\.SH$|^(200\d{3})\.SZ$")
A_SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def current_time() -> datetime:
    return datetime.now(SHANGHAI)


# SSE 2026 closure notice, 上证公告〔2025〕45号. Weekends remain closed.
HOLIDAYS_2026 = ((1, 1, 3), (2, 15, 23), (4, 4, 6), (5, 1, 5),
                 (6, 19, 21), (9, 25, 27), (10, 1, 7))


# Only register complete years verified against SSE exchange closure notices:
# 2025: https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml
# 2026: https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
EXCHANGE_CLOSURES = {
    2025: ((1, 1, 1), (1, 28, 31), (2, 1, 4), (4, 4, 6), (5, 1, 5),
           (5, 31, 31), (6, 1, 2), (10, 1, 8)),
    2026: HOLIDAYS_2026,
}


def is_trading_day(day: date) -> bool:
    if day.year not in EXCHANGE_CLOSURES:
        raise ValueError(f"Calendar year {day.year} not covered; update the exchange calendar")
    return day.weekday() < 5 and not any(
        day.month == month and first <= day.day <= last
        for month, first, last in EXCHANGE_CLOSURES[day.year]
    )


def latest_closed_session(now: datetime) -> date:
    local = now.astimezone(SHANGHAI)
    is_trading_day(local.date())  # Unknown current year must fail even on January 1.
    day = local.date() - timedelta(days=int(local.hour < 15))
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def parse_market_code(symbol: str, market: str) -> str:
    symbol = symbol.strip().upper()
    if market == "A" and A_SYMBOL_RE.fullmatch(symbol):
        # Reject clear B-board symbols even if format matches.
        if symbol.startswith("900") or symbol.startswith("200"):
            raise ValueError(f"B-share code passed as A share: {symbol}")
        return symbol
    if market == "B" and B_SYMBOL_RE.fullmatch(symbol):
        return symbol
    raise ValueError(f"invalid {market}-share stock code: {symbol}")


def ms_from_local_date(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=SHANGHAI).timestamp() * 1000)


BAR_FIELDS = ("date", "open", "high", "low", "close", "volume")


def bar_number(value: object) -> float | None:
    """Normalize provider numbers while retaining missing values for the audit."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean in numeric field")
    number = float(value)
    return None if math.isnan(number) else number


def convert_hithink_payload(payload: dict, symbols: list[str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Parse public batch schema. Missing symbols are NOT silently successful."""
    if not isinstance(payload, dict):
        raise ValueError("SCHEMA_CHANGED: outer object absent")
    code = payload.get("code")
    if type(code) is not int:
        raise ValueError("SCHEMA_CHANGED: expected integer API code")
    if code != 0:
        raise ValueError(f"API_ERROR code={code}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("item"), list):
        raise ValueError("SCHEMA_CHANGED: expected data.item list")
    if data.get("interval") != "1d" or data.get("adjust") != "forward":
        raise ValueError("SCHEMA_CHANGED: expected 1d/forward response")
    groups = {}
    duplicate_groups = set()
    for g in data["item"]:
        if isinstance(g, dict) and isinstance(g.get("thscode"), str):
            code = g["thscode"].upper()
            if code in groups:
                duplicate_groups.add(code)
            groups[code] = g
    parsed, errors = {}, {}
    for symbol in symbols:
        if symbol in duplicate_groups:
            errors[symbol] = "SCHEMA_CHANGED: duplicate symbol group"
            continue
        group = groups.get(symbol)
        if group is None:
            errors[symbol] = "SYMBOL_MISSING: API response did not return this symbol group"
            continue
        rows = group.get("item")
        if not isinstance(rows, list):
            errors[symbol] = "SCHEMA_CHANGED: symbol group lacks item list"
            continue
        if not rows:
            errors[symbol] = "EMPTY: no historical bars"
            continue
        out = []
        try:
            for r in rows:
                if not isinstance(r, dict):
                    raise ValueError("not an object")
                stamp = r["date_ms"]
                if stamp is not None and (type(stamp) is not int or stamp <= 0):
                    raise ValueError("invalid date_ms")
                out.append({
                    "date": (datetime.fromtimestamp(stamp / 1000, SHANGHAI).date().isoformat()
                             if stamp is not None else None),
                    **{k: bar_number(r[k + "_price"]) for k in ("open", "high", "low", "close")},
                    "volume": bar_number(r["volume"]),
                })
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as ex:
            errors[symbol] = f"SCHEMA_CHANGED: invalid K-line field ({type(ex).__name__})"
            continue
        parsed[symbol] = out
    return parsed, errors


def check_bars(symbol: str, bars: list[dict], provider: str, min_bars: int, *,
               expected_date: date | None = None, start_date: date | None = None) -> dict:
    result = {"provider": provider, "symbol": symbol, "source_is_live": True,
              "bar_count": len(bars), "status": "FAIL", "reason": None}
    try:
        if not bars:
            raise ValueError("EMPTY: no bars")
        result["null_counts"] = {
            k: sum(row.get(k) is None or
                   (isinstance(row[k], float) and math.isnan(row[k])) for row in bars)
            for k in BAR_FIELDS
        }
        if any(result["null_counts"].values()):
            raise ValueError("INVALID_BARS: null values in required fields")
        dates = [date.fromisoformat(row["date"]) for row in bars]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("INVALID_BARS: duplicate or unsorted dates")
        if any(d.weekday() >= 5 for d in dates):
            raise ValueError("INVALID_BARS: weekend trading date")
        if start_date and dates[0] < start_date:
            raise ValueError("INVALID_BARS: date before requested start")
        today = current_time().date()
        if dates[-1] > today:
            raise ValueError("INVALID_BARS: future date")
        for row in bars:
            o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
            if any(not math.isfinite(x) or x <= 0 for x in (o, h, l, c)):
                raise ValueError("INVALID_BARS: non-positive or nonfinite prices")
            if h < max(o, c, l) - 1e-8 or l > min(o, c, h) + 1e-8:
                raise ValueError("INVALID_BARS: OHLC range inconsistent")
            volume = float(row["volume"])
            if not math.isfinite(volume) or volume < 0:
                raise ValueError("INVALID_BARS: negative or nonfinite volume")
        if len(bars) < min_bars:
            raise ValueError(f"INSUFFICIENT_DATA: {len(bars)} < {min_bars} bars")
        if expected_date and dates[-1] != expected_date:
            if dates[-1] < expected_date:
                raise ValueError(f"STALE: expected completed session {expected_date}")
            raise ValueError(f"INVALID_BARS: date after last completed session {expected_date}")
        closes = [float(row["close"]) for row in bars]
        bundle = calculate_rsi_bundle(closes)
        result.update({"status": "PASS", "first_date": dates[0].isoformat(),
                       "last_date": dates[-1].isoformat(), "last_close": closes[-1],
                       "stale_calendar_days": (today - dates[-1]).days,
                       "recent_rsi": [
                           {"bar_date": dates[i].isoformat(), "close": closes[i],
                            **{name: values[i] for name, values in bundle.items()}}
                           for i in range(max(0, len(bars) - 3), len(bars))
                       ]})
    except (ValueError, TypeError, KeyError, OverflowError) as ex:
        result["reason"] = str(ex)
        if bars:
            result["first_date"] = str(bars[0].get("date", ""))
            result["last_date"] = str(bars[-1].get("date", ""))
    return result


class NoKeyRedirect(HTTPRedirectHandler):
    """A redirect is a probe failure. Never forward the API key."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProbeFailure(RuntimeError):
    def __init__(self, message: str, **metadata):
        super().__init__(message)
        self.metadata = metadata


def fetch_hithink(symbols: list[str], start: date, end: date, api_key: str) -> tuple[dict, dict]:
    if not api_key:
        raise RuntimeError("AUTH: HITHINK_FINANCE_API_KEY not configured")
    if not api_key.isascii() or not api_key.isprintable():
        raise ValueError("AUTH: API key contains invalid characters")
    if len(symbols) > 10:
        raise ValueError("Hithink batch maximum is 10 symbols")
    base = os.environ.get("HITHINK_FINANCE_BASE_URL", "https://fuyao.aicubes.cn").rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("HITHINK_FINANCE_BASE_URL must be HTTPS")
    params = urlencode({"thscodes": ",".join(symbols), "interval": "1d",
                        "start": ms_from_local_date(start),
                        "end": ms_from_local_date(end + timedelta(days=1)) - 1, "adjust": "forward"})
    url = f"{base}/api/a-share/prices/historical-batch?{params}"
    req = Request(url, headers={"X-api-key": api_key, "Accept": "application/json"})
    try:
        with build_opener(NoKeyRedirect()).open(req, timeout=20) as response:
            status = response.status
            raw = response.read()
    except HTTPError as ex:
        with ex:
            metadata = {"http_status": ex.code}
            try:
                body = json.loads(ex.read(65536))
                if isinstance(body, dict) and type(body.get("code")) is int:
                    metadata["api_code"] = body["code"]
            except (OSError, ValueError):
                pass
        raise ProbeFailure(f"HTTP_ERROR: status={ex.code}", **metadata) from None
    except (OSError, HTTPException) as ex:
        cause = ex.reason if isinstance(ex, URLError) else ex
        raise RuntimeError(f"NETWORK_UNAVAILABLE: {type(cause).__name__}") from None
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ProbeFailure("SCHEMA_CHANGED: server returned invalid JSON", http_status=status) from None
    if not isinstance(obj, dict):
        raise ProbeFailure("SCHEMA_CHANGED: server returned non-object JSON", http_status=status)
    metadata = {"http_status": status, "api_code": obj.get("code") if type(obj.get("code")) is int else None}
    request_id = obj.get("request_id")
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) and api_key not in request_id:
        metadata["request_id"] = request_id
    return obj, metadata


def fetch_akshare_b(symbol: str, start: date, end: date, *, metadata: dict | None = None) -> list[dict]:
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("DEPENDENCY_MISSING: pip install -e '.[probe]'") from None
    code = symbol.split(".")[0]
    prefix = "sh" if symbol.endswith(".SH") else "sz"
    params = {"symbol": prefix + code, "start_date": start.strftime("%Y%m%d"),
              "end_date": end.strftime("%Y%m%d"), "adjust": "qfq"}
    name = "stock_zh_b_daily"
    if not callable(getattr(ak, name, None)):
        name = "stock_zh_b_hist_sina"
        params["period"] = "daily"
    fetch = getattr(ak, name, None)
    if not callable(fetch):
        raise RuntimeError("SCHEMA_CHANGED: installed AKShare lacks a Sina B-share history function")
    if metadata is not None:
        metadata.update(provider_function=name, akshare_version=ak.__version__)
    try:
        frame = fetch(**params)
    except Exception as ex:
        # Requests can include URLs and headers in its exception messages.
        status = getattr(getattr(ex, "response", None), "status_code", None)
        if isinstance(ex, HTTPError):
            status = ex.code
        if isinstance(status, int):
            raise RuntimeError(f"HTTP_ERROR status={status}") from None
        causes = []
        cause = ex
        while cause is not None and type(cause).__name__ not in causes:
            causes.append(type(cause).__name__)
            cause = cause.__cause__ or cause.__context__
        network_errors = {"URLError", "ConnectionError", "ConnectionResetError", "Timeout",
                          "TimeoutError", "ConnectTimeout", "ReadTimeout", "SSLError",
                          "ProxyError", "NameResolutionError", "gaierror", "RemoteDisconnected"}
        category = "NETWORK_UNAVAILABLE" if network_errors.intersection(causes) else "UPSTREAM_ERROR"
        raise RuntimeError(f"{category}: AKShare raised {' / '.join(causes)}") from None
    if frame is None or frame.empty:
        raise RuntimeError("EMPTY: AKShare returned no B-share data")
    missing = [k for k in BAR_FIELDS if k not in frame.columns]
    if missing:
        raise RuntimeError(f"SCHEMA_CHANGED: AKShare B-share lacks {', '.join(missing)}")
    rows = []
    # Preserve missing values and source order so the audit sees the original defects.
    records = frame.astype(object).where(frame.notna(), None).to_dict("records")
    try:
        for record in records:
            day = record["date"]
            if isinstance(day, datetime):
                day = day.date()
            if isinstance(day, date):
                day = day.isoformat()
            rows.append({"date": day, **{k: bar_number(record[k]) for k in BAR_FIELDS[1:]}})
    except (ValueError, TypeError, OverflowError) as ex:
        raise RuntimeError(f"SCHEMA_CHANGED: invalid AKShare number ({type(ex).__name__})") from None
    return rows


def create_demo() -> tuple[dict, list[dict]]:
    t = current_time().date()
    start = t - timedelta(days=220)
    d, output = start, []
    while d <= t:
        if d.weekday() < 5:
            val = 100 + len(output) * .17 + (len(output) % 11 - 5) * .08
            output.append({"date": d.isoformat(), "open": val, "high": val + .6,
                           "low": val - .6, "close": val + .1, "volume": 10000})
        d += timedelta(days=1)
    result = check_bars("DEMO000", output, "DEMO_FAKE_DATA", 100)
    result["source_is_live"] = False
    result["status"] = "DEMO_ONLY"
    result["warning"] = "Synthetic example, NOT a real stock quote"
    return result, output


def export_rows_csv(destination: Path, symbol: str, rows: list[dict]) -> str:
    destination.mkdir(parents=True, exist_ok=True)
    safe_name = symbol.replace(".", "_")
    filepath = destination / f"probe_{safe_name}.csv"
    fields = ("date", "open", "high", "low", "close", "volume")
    with filepath.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})
    return str(filepath)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Market data feasibility probe, never trades")
    p.add_argument("--provider", choices=("demo", "hithink", "akshare-b", "all"), default="demo")
    p.add_argument("--a", nargs="+", default=["600519.SH"], help="A-share full thscode(s)")
    p.add_argument("--b", nargs="+", default=["900901.SH"], help="B-share full code(s)")
    p.add_argument("--days", type=int, default=240, help="calendar days of history")
    p.add_argument("--min-bars", type=int, default=100)
    p.add_argument("--expected-date", type=date.fromisoformat,
                   help="latest completed exchange session (YYYY-MM-DD), for a calendar override")
    p.add_argument("--export-csv", action="store_true", help="save real OHLC to ignored reports/")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args(argv)
    if args.days < 35 or args.days > 3650 or args.min_bars < 5:
        p.error("days must be 35..3650 and min-bars >= 5")

    now = current_time()
    start = now.date() - timedelta(days=args.days)
    end = now.date()
    if args.provider != "demo":
        try:
            end = args.expected_date or latest_closed_session(now)
        except ValueError as ex:
            p.error(str(ex))
        if (end < start or end > now.date() or end.weekday() >= 5
                or (end == now.date() and now.hour < 15)):
            p.error("expected-date must be a completed session within the requested window")
    report = {"probe_version": "0.2", "provider_mode": args.provider,
              "generated_at": now.isoformat(), "time_zone": "Asia/Shanghai",
              "requested_start": start.isoformat(), "requested_end": end.isoformat(),
              "expected_bar_date": end.isoformat() if args.provider != "demo" else None,
              "min_bars": args.min_bars, "results": [],
              "notes": ["Not a trading system", "No credentials persisted in this report"]}
    results = report["results"]
    started = time.monotonic()

    if args.provider == "demo":
        row, bars = create_demo()
        results.append(row)
        if args.export_csv:
            row["csv_path"] = export_rows_csv(args.output.parent, "DEMO000", bars)
    else:
        if args.provider in ("hithink", "all"):
            try:
                a_symbols = list(dict.fromkeys(parse_market_code(s, "A") for s in args.a))
                # Batching rather than silently truncating when > 10.
                key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
                for idx in range(0, len(a_symbols), 10):
                    batch = a_symbols[idx:idx+10]
                    if not key:
                        for symbol in batch:
                            results.append({"provider": "hithink", "symbol": symbol,
                                            "status": "NOT_RUN_NO_CREDENTIALS",
                                            "reason": "NOT_RUN_NO_CREDENTIALS: HITHINK_FINANCE_API_KEY missing",
                                            "duration_seconds": 0})
                        continue
                    batch_start = time.monotonic()
                    metadata = {}
                    try:
                        data, metadata = fetch_hithink(batch, start, end, key)
                        parsed, errors = convert_hithink_payload(data, batch)
                        for symbol in batch:
                            if symbol in errors:
                                results.append({"provider": "hithink", "symbol": symbol,
                                                "status": "FAIL", "reason": errors[symbol],
                                                "duration_seconds": round(time.monotonic() - batch_start, 3),
                                                **metadata})
                            else:
                                bars = parsed[symbol]
                                row = check_bars(symbol, bars, "hithink", args.min_bars,
                                                 expected_date=end, start_date=start)
                                row.update(metadata)
                                row["duration_seconds"] = round(time.monotonic() - batch_start, 3)
                                if args.export_csv and row["status"] == "PASS":
                                    row["csv_path"] = export_rows_csv(args.output.parent, symbol, bars)
                                results.append(row)
                    except (RuntimeError, ValueError) as ex:
                        reason = str(ex)
                        for symbol in batch:
                            results.append({"provider": "hithink", "symbol": symbol,
                                            "status": "FAIL", "reason": reason, **metadata,
                                            **getattr(ex, "metadata", {}),
                                            "duration_seconds": round(time.monotonic() - batch_start, 3)})
            except ValueError as ex:
                results.append({"provider": "hithink", "status": "FAIL", "reason": str(ex)})

        if args.provider in ("akshare-b", "all"):
            for raw_symbol in args.b:
                started_b = time.monotonic()
                metadata = {}
                try:
                    symbol = parse_market_code(raw_symbol, "B")
                    bars = fetch_akshare_b(symbol, start, end, metadata=metadata)
                    row = check_bars(symbol, bars, "akshare-b", args.min_bars,
                                     expected_date=end, start_date=start)
                    row.update(metadata)
                    row["duration_seconds"] = round(time.monotonic() - started_b, 3)
                    if args.export_csv and row["status"] == "PASS":
                        row["csv_path"] = export_rows_csv(args.output.parent, symbol, bars)
                    results.append(row)
                except (RuntimeError, ValueError) as ex:
                    results.append({"provider": "akshare-b", "symbol": raw_symbol,
                                    "status": "FAIL", "reason": str(ex), **metadata,
                                    "duration_seconds": round(time.monotonic() - started_b, 3)})
                # Avoid accidental bursts to public third-party source.
                if len(args.b) > 1:
                    time.sleep(1.0)

    for row in results:
        if row["provider"] in ("hithink", "akshare-b"):
            row.update(interval="1d", adjustment="forward" if row["provider"] == "hithink" else "qfq")
    total_pass = sum(r["status"] == "PASS" for r in results)
    total_fail = sum(r["status"] == "FAIL" for r in results)
    not_run = sum(r["status"].startswith("NOT_RUN") for r in results)
    report["sources_are_live"] = any(r.get("source_is_live") for r in results)
    report["summary"] = {"pass": total_pass, "fail": total_fail,
                         "not_run": not_run, "demo_only": sum(r["status"] == "DEMO_ONLY" for r in results),
                         "duration_seconds": round(time.monotonic() - started, 3)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}")
    for r in results:
        print(f"[{r['status']}] {r.get('provider')} {r.get('symbol','')}: "
              f"{r.get('bar_count', 0)} bars; {r.get('reason') or r.get('last_date', 'NO_LIVE_DATA')}")
    return 2 if total_fail or not_run else 0


if __name__ == "__main__":
    sys.exit(main())

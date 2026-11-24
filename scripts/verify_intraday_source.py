"""Capture real-session quotes in a separate ledger and compare them to a terminal."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from sqlalchemy import select

from backend.app.db import DB, Holding
from backend.app.intraday import quote_values, UnusableQuote
from backend.app.market_usage import usage_summary
from backend.app.scheduler import session_open
from backend.app.snapshot_provider import get_snapshots
from scripts.market_probe import latest_closed_session, SHANGHAI


def private_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def inspection_db(directory, holdings, backup):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = directory / "source-validation.sqlite3"
    if backup.resolve() == ledger.resolve():
        raise ValueError("验证台账与只读备份必须使用不同文件")
    if ledger.exists():
        with closing(sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True)) as existing:
            if existing.execute("PRAGMA application_id").fetchone()[0] != 0x53525631:
                raise ValueError("指定目录中已存在其它 SQLite 库，请选择专用验证目录")
    db = DB("sqlite:///" + str(ledger.resolve()))
    db.migrate()
    with db.engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA application_id=0x53525631")
    with db.sessions.begin() as session:
        for symbol, market in holdings:
            if session.scalar(select(Holding.id).where(Holding.symbol == symbol)) is None:
                session.add(Holding(symbol=symbol, market=market))
    return db


def capture(backup, directory, *, probe_b=False):
    started = datetime.now(SHANGHAI)
    report = {"started_at": started.isoformat(), "status": "AWAITING_SESSION", "samples": {}}
    if not session_open(started):
        return report
    previous = latest_closed_session(started.replace(hour=0, minute=0, second=0, microsecond=0))
    backup = backup.resolve(strict=True)
    with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as reader:
        reader.row_factory = sqlite3.Row
        reader.execute("BEGIN")
        stocks = [tuple(row) for row in reader.execute(
            "SELECT symbol,market FROM holdings WHERE enabled=1 AND deleted=0 ORDER BY symbol")]
        settings = SimpleNamespace(**dict(reader.execute(
            "SELECT overbought,high_level FROM settings WHERE id=1").fetchone()))
        db = inspection_db(directory, stocks, backup)
        try:
            quotes = get_snapshots(db, stocks, started, "normal", probe_b=probe_b)
            observed = datetime.now(SHANGHAI)
            report.update(status="AWAITING_REFERENCE", observed_at=observed.isoformat(),
                          usage=usage_summary(db, observed))
            for symbol, market in stocks:
                quote = quotes.get(symbol, {})
                sample = report["samples"][symbol] = {"market": market, "source_quote": quote}
                provider, adjust = ("hithink", "forward") if market == "A" else ("akshare-b", "qfq")
                bars = [SimpleNamespace(**dict(row)) for row in reader.execute(
                    """SELECT bar_date,close,rsi6,rsi12 FROM daily_bars WHERE symbol=? AND provider=?
                       AND adjust=? AND bar_date BETWEEN ? AND ? ORDER BY bar_date""", (
                        symbol,provider,adjust,(previous-timedelta(days=240)).isoformat(),previous.isoformat()))]
                try:
                    if not session_open(observed) or observed.date() != started.date():
                        raise UnusableQuote("请求完成时已超出交易时段")
                    stamp, prices, change, result = quote_values(
                        quote, now=observed, holding=SimpleNamespace(symbol=symbol, market=market),
                        prior_bars=bars, settings=settings)
                    sample.update(detected_at=observed.isoformat(), batch_time=quote.get("batch_time"),
                                  trade_time=quote.get("quote_time"), effective_time=stamp.isoformat(),
                                  price=prices["price"], change_pct=change,
                                  **{f"rsi{n}": result[f"rsi{n}"] for n in (6,12,24)})
                except UnusableQuote as exc:
                    sample["error"] = str(exc)
        finally:
            db.engine.dispose()
    return report


def compare(captured, reference):
    verdict = {"started_at": captured.get("started_at"), "status": "INCOMPLETE", "stocks": {}}
    for symbol, sample in captured["samples"].items():
        row = verdict["stocks"][symbol] = {"matched": False}
        ref = reference.get(symbol)
        if sample.get("error") or not (sample.get("detected_at") or sample.get("quote_time")):
            row["reason"] = sample.get("error") or "缺少已核实盘中行情"
            continue
        if not isinstance(ref, dict) or not isinstance(ref.get("source"), str) or not ref["source"].strip():
            row["reason"] = "缺少独立终端来源或同一时点记录"
            continue
        try:
            t = datetime.fromisoformat(ref.get("observed_at") or ref["quote_time"])
            if t.tzinfo is None:
                raise ValueError
            seconds = abs((t - datetime.fromisoformat(sample.get("detected_at") or sample["quote_time"])).total_seconds())
            fields = ("price","rsi6","rsi12","rsi24")
            deltas = {k: abs(float(sample[k]) - float(ref[k])) for k in fields}
            if not all(math.isfinite(delta) for delta in deltas.values()):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            row["reason"] = "参考数据缺少带时区时间或有效的价格/RSI"
            continue
        matched = seconds <= 300 and deltas["price"] <= max(.02, float(ref["price"]) * .002) and max(deltas[k] for k in fields[1:]) <= .5
        row.update(matched=matched, time_delta_s=seconds, absolute_deltas=deltas,
                   source=ref["source"], reason="终端近时点抽样对照通过；仍需跨时点确认行情变化" if matched else "终端时间、价格或 RSI 超出容差")
    if verdict["stocks"] and all(row["matched"] for row in verdict["stocks"].values()):
        verdict["status"] = "MATCHED"
    return verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    run = modes.add_parser("capture", help="仅在真实交易时段访问行情，读取最新数据库备份")
    run.add_argument("--backup", type=Path, required=True)
    run.add_argument("--ledger-dir", type=Path, required=True, help="每次复用同一个私有验证台账目录")
    run.add_argument("--probe-b", action="store_true", help="额外探测一次 B 股候选快照")
    check = modes.add_parser("compare", help="离线与独立终端同一时点采样对照")
    check.add_argument("--capture", type=Path, required=True)
    check.add_argument("--reference", type=Path, required=True)
    for mode in (run, check):
        mode.add_argument("--out", type=Path, required=True, help="全新的私有 JSON 报告路径")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out 已存在，请指定新文件")
    os.umask(0o077)
    if args.mode == "capture":
        try:
            result = capture(args.backup,args.ledger_dir,probe_b=args.probe_b)
        except ValueError as exc:
            if "Calendar year" not in str(exc):
                raise
            result = {"status":"CALENDAR_UNVERIFIED","samples":{}}
    else:
        result = compare(json.loads(args.capture.read_text()),json.loads(args.reference.read_text()))
    private_report(args.out, result)
    print(result["status"])


if __name__ == "__main__":
    main()

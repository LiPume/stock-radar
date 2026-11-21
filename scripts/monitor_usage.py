"""从 Stock Radar V1.1 SQLite 台账只读汇总行情调用与微信投递，默认最近七个北京时间自然日。"""
import argparse
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


PROVIDERS = ("hithink", "akshare-b")


def usage_report(database: Path, days: int, until: date) -> str:
    if not 1 <= days <= 366:
        raise ValueError("统计天数必须在 1 至 366 之间")
    first = until - timedelta(days=days - 1)
    bounds = (first.isoformat(), (until + timedelta(days=1)).isoformat())
    # mode=ro sees committed WAL data on a live database, including today's usage.
    uri = database.resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        market = {
            (day, provider): counts for day, provider, *counts in db.execute("""
                SELECT substr(at,1,10), provider, sum(kind='HTTP'),
                       sum(kind='HTTP' AND status='OK'),
                       sum(kind='HTTP' AND status NOT IN ('OK','PENDING')),
                       sum(kind='HTTP' AND status='PENDING'),
                       sum(kind='HTTP' AND status='RATE_LIMIT'), sum(kind='CACHE')
                  FROM market_usage
                 WHERE at>=? AND at<? AND provider IN (?,?)
                 GROUP BY substr(at,1,10), provider
            """, (*bounds, *PROVIDERS))
        }
        deliveries = {
            day: counts for day, *counts in db.execute("""
                SELECT substr(created_at,1,10), count(*), sum(attempts),
                       sum(status='SENT'), sum(status='FAILED'), sum(status='UNKNOWN'),
                       sum(status='SENDING'), sum(status='SKIPPED'),
                       sum(kind='brief' AND status='SENT')
                  FROM notification_deliveries
                 WHERE created_at>=? AND created_at<?
                 GROUP BY substr(created_at,1,10)
            """, bounds)
        }

    dates = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    lines = [
        f"Stock Radar 用量观察 · {first} 至 {until}（北京时间）",
        "",
        "| 日期 | 行情源 | 物理 HTTP | 成功 | 失败 | 未结算 | 其中限流 | 缓存命中 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for day in dates:
        for provider in PROVIDERS:
            counts = market.get((day, provider), [0] * 6)
            lines.append("| " + " | ".join(map(str, [day, provider, *counts])) + " |")
    for provider in PROVIDERS:
        by_day = [market.get((day, provider), [0] * 6) for day in dates]
        totals = [sum(values) for values in zip(*by_day)]
        lines.append("| " + " | ".join(map(str, ["合计", provider, *totals])) + " |")
    lines.extend([
        "",
        "| 日期 | 通知记录 | 发送尝试 | 服务商已接收 | 失败 | 回执未知 | 发送中 | 跳过 | 其中简报已接收 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for day in dates:
        counts = deliveries.get(day, [0] * 8)
        lines.append("| " + " | ".join(map(str, [day, *counts])) + " |")
    by_day = [deliveries.get(day, [0] * 8) for day in dates]
    totals = [sum(values) for values in zip(*by_day)]
    lines.append("| " + " | ".join(map(str, ["合计", *totals])) + " |")
    lines.append("\n行情未结算表示请求已登记且未取得最终结果。零值表示台账中没有对应记录。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="生产数据库或备份文件的路径")
    parser.add_argument("--days", type=int, default=7, help="统计自然日数，默认 7")
    parser.add_argument("--until", type=date.fromisoformat, help="截至日期 YYYY-MM-DD，默认北京时间今日")
    args = parser.parse_args()
    try:
        until = args.until or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        print(usage_report(args.database, args.days, until))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"用量统计失败，请核对文件与 V1.1 数据库版本。{exc}\n")


if __name__ == "__main__":
    main()

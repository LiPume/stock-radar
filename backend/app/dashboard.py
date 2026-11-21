"""Read-side projections for the PWA. All quotes are read from local SQLite."""
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from .db import CheckRun, DailyBar, IntradayReading
from .securities import currency_for, verified_name


def closing_bars(symbol: str, market: str):
    preferred = "hithink" if market == "A" else "akshare-b"
    candidate = aliased(DailyBar)
    # Older adapters may use either forward-adjustment label. Pick one official
    # reading per day so two sources cannot turn yesterday's change into zero.
    chosen = (select(candidate.id)
              .where(candidate.symbol == symbol, candidate.bar_date == DailyBar.bar_date,
                     candidate.adjust.in_(("forward", "qfq")))
              .order_by((candidate.provider == preferred).desc(), candidate.id.desc())
              .limit(1).correlate(DailyBar).scalar_subquery())
    return select(DailyBar).where(DailyBar.symbol == symbol, DailyBar.id == chosen).order_by(DailyBar.bar_date.desc())


def quote_fields(session, row, data: dict, now: datetime) -> None:
    bars = session.scalars(closing_bars(row.symbol, row.market).limit(2)).all()
    close = bars[0] if bars else None
    today = now.date().isoformat()
    data.update(security_name=verified_name(row.symbol), price=None, change_pct=None,
                currency=currency_for(row.symbol, row.market), quote_time=None,
                price_context="CLOSE", close_date=close.bar_date if close else None)
    data["close_rsi"] = {field: getattr(close, field, None) for field in ("rsi6", "rsi12", "rsi24")}
    if close:
        change = None
        if len(bars) == 2 and bars[1].close > 0:
            change = (close.close / bars[1].close - 1) * 100
        data.update(price=close.close, change_pct=change, quote_time=close.bar_date)
        if data["data_status"] != "OK":
            data.update(price_context="LAST_CLOSE", rsi6=None, rsi12=None, rsi24=None)
    if not row.enabled or (close and close.bar_date >= today) or (now.hour, now.minute) >= (15, 45):
        return
    reading = session.scalar(select(IntradayReading).where(IntradayReading.holding_id == row.id,
                             IntradayReading.trade_date == today).order_by(IntradayReading.id.desc()).limit(1))
    if reading is None:
        return
    data.update(data_status=reading.data_status, status=reading.status, reason=reading.reason,
                security_name=reading.name or data["security_name"], detected_at=reading.observed_at)
    if reading.data_status == "OK":
        data.update(price_context="INTRADAY", price=reading.price, change_pct=reading.change_pct,
                    quote_time=reading.quote_time, rsi6=reading.rsi6, rsi12=reading.rsi12,
                    rsi24=reading.rsi24, currency=reading.currency, provider=reading.provider)
    else:
        data.update(price_context="LAST_CLOSE", rsi6=None, rsi12=None, rsi24=None)


def run_counts(session, day: str) -> dict:
    counts = dict(session.execute(select(CheckRun.state, func.count(CheckRun.id))
                  .where(CheckRun.started_at.startswith(day)).group_by(CheckRun.state)).all())
    return {"actual":sum(counts.values()), "success":counts.get("SUCCESS", 0),
            "partial":counts.get("PARTIAL", 0), "failed":counts.get("FAILED", 0),
            "running":counts.get("RUNNING", 0)}

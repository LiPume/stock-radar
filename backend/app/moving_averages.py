"""Simple daily price averages over locally stored forward-adjusted closing prices."""
from collections import deque
from math import fsum, isfinite

from sqlalchemy import select
from sqlalchemy.orm import aliased

from .db import DailyBar

PERIODS = (5, 10, 20)
WARMUP_BARS = max(PERIODS) - 1


def closing_bars(symbol: str, market: str):
    preferred = "hithink" if market == "A" else "akshare-b"
    preferred_adjust = "forward" if market == "A" else "qfq"
    candidate = aliased(DailyBar)
    # Retain legacy forward-adjusted sources, choosing one official bar per day.
    chosen = (select(candidate.id)
              .where(candidate.symbol == symbol, candidate.bar_date == DailyBar.bar_date,
                     candidate.adjust.in_(("forward", "qfq")))
              .order_by((candidate.provider == preferred).desc(),
                        (candidate.adjust == preferred_adjust).desc(), candidate.id.desc())
              .limit(1).correlate(DailyBar).scalar_subquery())
    return select(DailyBar).where(DailyBar.symbol == symbol, DailyBar.id == chosen).order_by(DailyBar.bar_date.desc())


def _empty():
    return {f"ma{n}": None for n in PERIODS}


def rolling_averages(prices):
    """Yield aligned results in ascending date order. Never splice two price bases."""
    window = deque(maxlen=max(PERIODS))
    previous_basis = None
    for provider, adjust, close in prices:
        basis = (provider, adjust)
        if basis != previous_basis:
            window.clear()
            previous_basis = basis
        if close is None or isinstance(close, bool) or not isfinite(close) or close <= 0:
            window.clear()
        else:
            window.append(close)
        # Each bounded window is only 20 prices; fsum avoids accumulated subtraction error.
        values = list(window)
        yield {f"ma{n}": fsum(values[-n:]) / n if len(values) >= n else None for n in PERIODS}


def bar_averages(bars):
    return rolling_averages((bar.provider, bar.adjust, bar.close) for bar in bars)


def latest_averages(session, symbol, market, *, day: str, provisional_price: float | None = None):
    """Read through day, or append one verified provisional price to earlier formal bars."""
    if provisional_price is None:
        query = closing_bars(symbol, market).where(DailyBar.bar_date <= day).limit(max(PERIODS))
        recent = session.execute(query.with_only_columns(DailyBar.provider, DailyBar.adjust, DailyBar.close)).all()
        prices = list(reversed(recent))
    else:
        # Valid intraday prices use the same source and adjustment as the RSI engine.
        provider, adjust = ("hithink", "forward") if market == "A" else ("akshare-b", "qfq")
        closes = session.scalars(select(DailyBar.close).where(
            DailyBar.symbol == symbol, DailyBar.provider == provider, DailyBar.adjust == adjust,
            DailyBar.bar_date < day).order_by(DailyBar.bar_date.desc()).limit(WARMUP_BARS)).all()
        prices = [(provider, adjust, price) for price in reversed(closes)]
        prices.append((provider, adjust, provisional_price))
    return list(rolling_averages(prices))[-1] if prices else _empty()

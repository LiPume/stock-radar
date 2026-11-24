"""Shanghai exchange schedule. Intraday/brief settings take effect without restarting."""
from datetime import datetime, time, timedelta
import os
import re

from apscheduler.schedulers.background import BackgroundScheduler
from scripts.market_probe import SHANGHAI, is_trading_day
from .db import Setting

CLOSE_MINUTE = 15 * 60 + 45


def intraday_validated() -> bool:
    return bool(verified_symbols()) or os.getenv("INTRADAY_VALIDATED", "false").lower() in ("true", "1", "yes", "on")


def verified_symbols() -> set[str]:
    """Explicit per-holding approval after live source, date and adjustment checks."""
    raw = os.getenv("INTRADAY_VERIFIED_SYMBOLS", "")
    return {symbol for part in raw.split(",") if (symbol := part.strip().upper())
            and re.fullmatch(r"\d{6}\.(?:SH|SZ)", symbol)}


def symbol_intraday_validated(symbol: str, market: str) -> bool:
    approved = verified_symbols()
    if approved:
        return symbol in approved
    # Compatibility for an existing explicitly validated A-share deployment.
    return market == "A" and os.getenv("INTRADAY_VALIDATED", "false").lower() in ("true", "1", "yes", "on")


def session_open(now: datetime) -> bool:
    now = now.astimezone(SHANGHAI)
    m = now.hour * 60 + now.minute
    return is_trading_day(now.date()) and (570 <= m <= 690 or 780 <= m < 900)


def scan_minutes(interval: int) -> set[int]:
    """First morning interval after the open, plus the afternoon open."""
    if interval not in (15, 30, 60):
        raise ValueError("interval must be 15, 30 or 60")
    return set(range(570 + interval, 691, interval)) | set(range(780, 900, interval))


def minute_of(value: str) -> int:
    clock = time.fromisoformat(value)
    return clock.hour * 60 + clock.minute


def plan_status(db, now: datetime) -> dict:
    with db.sessions() as session:
        prefs = session.get(Setting, 1)
    minutes = {CLOSE_MINUTE}
    from .db import Holding
    with db.sessions() as session:
        holdings = session.query(Holding).filter(Holding.enabled, ~Holding.deleted).all()
    retry_pending = any(h.data_status == "STALE" for h in holdings)
    verified_count = sum(symbol_intraday_validated(h.symbol, h.market) for h in holdings)
    verified = verified_count > 0
    if prefs.intraday_enabled and verified:
        minutes.update(scan_minutes(prefs.interval_minutes))
    report_minute = minute_of(prefs.brief_time) if prefs.brief_enabled else None
    if report_minute is not None:
        minutes.add(report_minute)
    local = now.astimezone(SHANGHAI)
    next_scan = next_brief = None
    planned_today = 0
    for offset in range(367):
        day = local.date() + timedelta(days=offset)
        try:
            trading = is_trading_day(day)
        except ValueError:
            break
        if not trading:
            continue
        day_minutes = minutes | ({16 * 60 + 15, 16 * 60 + 45} if offset == 0 and retry_pending else set())
        if offset == 0:
            planned_today = len(day_minutes)
        midnight = datetime.combine(day, time(), SHANGHAI)
        for m in sorted(day_minutes):
            dt = midnight + timedelta(minutes=m)
            if dt > local:
                if next_scan is None:
                    next_scan = dt.isoformat()
                if m == report_minute and next_brief is None:
                    next_brief = dt.isoformat()
        if next_scan and (next_brief or report_minute is None):
            break
    return {"next_scan_at": next_scan, "next_brief_at": next_brief, "planned_today": planned_today,
            "intraday_validated": verified, "intraday_count": verified_count,
            "close_only_count": len(holdings) - verified_count,
            "market_open": session_open(local) if local.year <= 2026 else False}


def make_scheduler(service):
    scheduler = BackgroundScheduler(timezone=SHANGHAI)
    opts = dict(day_of_week="mon-fri", coalesce=True, max_instances=1, misfire_grace_time=900)
    for trigger, hour, minute in (("scheduled", 15, 45), ("retry_1", 16, 15), ("retry_2", 16, 45)):
        scheduler.add_job(service.scheduled_tick, "cron", args=[trigger], id=trigger,
                          hour=hour, minute=minute, **opts)
    scheduler.add_job(service.scheduled_tick, "cron", args=["intraday_clock"], id="intraday_clock",
                      hour="9-23", minute="*", **opts)
    return scheduler

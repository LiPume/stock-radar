"""Validate a temporary session bar against verified, cached forward-adjusted history."""
from datetime import datetime, time, timedelta
from math import isclose, isfinite

from sqlalchemy import select

from scripts import market_probe as probe
from stock_radar.rules.engine import evaluate_history
from .db import CheckRun, DailyBar, Holding, IntradayReading, RiskTransition
from .scheduler import session_open, symbol_intraday_validated
from .securities import currency_for

RULES = {"RSI_OVERBOUGHT_CROSS": "WATCH", "RSI_HIGH_DEATH_CROSS": "ALERT"}


class UnusableQuote(ValueError):
    pass


def quote_values(quote, *, now, holding, prior_bars, settings):
    """Return a normalized temporary bar and evaluation, never change official bars."""
    if not quote or quote.get("error"):
        raise UnusableQuote((quote or {}).get("error") or "行情源未返回该标的")
    row_time = quote.get("quote_time")
    batch_time = quote.get("batch_time")
    if not row_time and not batch_time:
        raise UnusableQuote("行情缺少逐股时间和批次更新时间")
    try:
        stamp = datetime.fromisoformat(row_time or batch_time)
    except (TypeError, ValueError):
        raise UnusableQuote("行情时间无效") from None
    day = now.astimezone(probe.SHANGHAI).date()
    if stamp.tzinfo is None or stamp.astimezone(probe.SHANGHAI).date() != day or not session_open(stamp):
        raise UnusableQuote("行情日期或交易时段不符")
    if not timedelta(0) <= now - stamp <= timedelta(minutes=5):
        raise UnusableQuote("行情时间陈旧或处于未来")
    if batch_time:
        try:
            batch = datetime.fromisoformat(batch_time)
        except (TypeError, ValueError):
            raise UnusableQuote("批次更新时间无效") from None
        if batch.tzinfo is None or batch.astimezone(probe.SHANGHAI).date() != day or not timedelta(0) <= now - batch <= timedelta(minutes=5):
            raise UnusableQuote("批次更新时间不是本轮交易时段")
    source_day = quote.get("source_trade_date")
    if source_day and str(source_day)[:10] != day.isoformat():
        raise UnusableQuote("逐股行情交易日与当前交易日不符")
    if quote.get("currency") != currency_for(holding.symbol, holding.market):
        raise UnusableQuote("行情币种不符")
    fields = ("price", "prev_close", "open", "high", "low")
    if any(isinstance(quote.get(field), bool) for field in fields):
        raise UnusableQuote("行情价格字段无效")
    try:
        prices = {field: float(quote[field]) for field in fields}
    except (KeyError, TypeError, ValueError, OverflowError):
        raise UnusableQuote("缺少有效的行情价格") from None
    if not all(isfinite(v) and v > 0 for v in prices.values()):
        raise UnusableQuote("行情价格无效")
    if not (prices["low"] <= prices["price"] <= prices["high"] and
            prices["low"] <= prices["open"] <= prices["high"]):
        raise UnusableQuote("行情高低价与当前价不一致")
    volume = quote.get("volume")
    if volume is not None:
        try:
            if not isfinite(float(volume)) or float(volume) <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise UnusableQuote("成交量缺失、无效或该股未成交") from None
    prior = probe.latest_closed_session(datetime.combine(day, datetime.min.time(), probe.SHANGHAI)).isoformat()
    if len(prior_bars) < 99 or prior_bars[-1].bar_date != prior:
        raise UnusableQuote("前一交易日的正式日线或历史长度不足")
    if quote.get("adjustment_verified") is not True or not isclose(
            prices["prev_close"], prior_bars[-1].close, rel_tol=1e-8, abs_tol=1e-7):
        raise UnusableQuote("行情与前复权历史的可比价格或除权口径未核实")
    closes = [b.close for b in prior_bars]
    # A verified raw unit maps 1:1 to the last historical forward-adjusted close.
    # The current bar is appended in memory exactly once for each evaluation.
    result = evaluate_history([*closes, prices["price"]], overbought=settings.overbought,
                              high_level=settings.high_level)
    for field in ("rsi6", "rsi12"):
        prev = getattr(prior_bars[-1], field)
        calc = result["prev_" + field]
        if prev is None or calc is None or not isclose(calc, prev, rel_tol=1e-9, abs_tol=1e-8):
            raise UnusableQuote("历史 RSI 基线未通过复算")
    change = (prices["price"] / prices["prev_close"] - 1) * 100
    reported_change = quote.get("change_pct")
    if reported_change is not None:
        try:
            if not isfinite(float(reported_change)) or abs(float(reported_change) - change) > 0.2:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise UnusableQuote("行情涨跌幅与现价、昨收不一致") from None
    return stamp, prices, change, result


def trading_minutes_since(earlier: datetime, later: datetime) -> int:
    day = later.astimezone(probe.SHANGHAI).date()
    if earlier.astimezone(probe.SHANGHAI).date() != day:
        return 0
    total = 0
    for start, end in ((time(9, 30), time(11, 30)), (time(13), time(15))):
        left = max(earlier, datetime.combine(day, start, probe.SHANGHAI))
        right = min(later, datetime.combine(day, end, probe.SHANGHAI))
        total += max(0, int((right - left).total_seconds() // 60))
    return total


def record_transitions(session, holding, run_id, day, result, evidence, now, *, closing=False):
    """An invalid snapshot never enters here, so it cannot clear a real occurrence."""
    for rule, severity in RULES.items():
        last = session.scalar(select(RiskTransition).where(
            RiskTransition.holding_id == holding.id, RiskTransition.trade_date == day,
            RiskTransition.rule_code == rule).order_by(RiskTransition.id.desc()).limit(1))
        crossed = rule in result["events"]
        if crossed and closing:
            if last and last.stage == "CONFIRMED":
                continue
            stage = "CONFIRMED"
        elif crossed:
            if last and last.stage != "CLEARED":
                continue
            stage = "PROVISIONAL"
        elif last and last.stage == "PROVISIONAL":
            stage = "CLEARED"
        else:
            continue
        session.add(RiskTransition(holding_id=holding.id, symbol=holding.symbol, run_id=run_id,
            trade_date=day, rule_code=rule, severity=severity, stage=stage,
            parent_id=last.id if last else None, evidence={**evidence, "phase":"close" if closing else "intraday"},
            created_at=now.isoformat()))


def run_intraday(db, run_id, now, settings, fetch, priority, blocked_reason=None, clock=None):
    day = now.astimezone(probe.SHANGHAI).date().isoformat()
    with db.sessions() as session:
        targets = session.scalars(select(Holding).where(Holding.enabled, ~Holding.deleted)
                                  .order_by(Holding.id)).all()
    quotes = {}
    eligible = [h for h in targets if symbol_intraday_validated(h.symbol, h.market)]
    if not blocked_reason and eligible:
        try:
            quotes = fetch(db, [(h.symbol,h.market) for h in eligible], now, priority)
        except Exception as exc:
            blocked_reason = f"盘中行情获取失败 ({type(exc).__name__})"
    observed = clock().astimezone(probe.SHANGHAI) if clock else now
    if observed.date() != now.date():
        blocked_reason = "盘中任务跨交易日，快照无法核实"
    for target in targets:
        quote = quotes.get(target.symbol) or {}
        with db.sessions.begin() as session:
            holding = session.get(Holding, target.id)
            if holding.deleted or not holding.enabled:
                continue
            run = session.get(CheckRun, run_id)
            provider = quote.get("provider") or ("hithink" if holding.market == "A" else "akshare-b")
            row = IntradayReading(run_id=run_id, holding_id=holding.id, symbol=holding.symbol, trade_date=day,
                observed_at=observed.isoformat(), provider=provider, name=quote.get("name"),
                currency=currency_for(holding.symbol, holding.market), status="DATA_UNAVAILABLE",data_status="DATA_UNAVAILABLE")
            session.add(row)
            if not symbol_intraday_validated(holding.symbol, holding.market):
                row.status = row.data_status = "CLOSE_ONLY"
                row.reason = "该股票仅收盘监控，盘中行情尚待交易时段验证"
                continue
            try:
                if blocked_reason:
                    raise UnusableQuote(blocked_reason)
                source, adjust = ("hithink", "forward") if holding.market == "A" else ("akshare-b", "qfq")
                previous = probe.latest_closed_session(datetime.combine(now.date(), datetime.min.time(), probe.SHANGHAI))
                # Rebuild the exact window that produced the previous official RSI.
                history_start = (previous - timedelta(days=240)).isoformat()
                bars = session.scalars(select(DailyBar).where(DailyBar.symbol == target.symbol,
                    DailyBar.provider == source, DailyBar.adjust == adjust,
                    DailyBar.bar_date >= history_start, DailyBar.bar_date <= previous.isoformat())
                    .order_by(DailyBar.bar_date)).all()
                stamp, prices, change, result = quote_values(quote, now=observed, holding=holding,
                                                          prior_bars=bars, settings=settings)
                volume = quote.get("volume")
                if volume is not None:
                    previous_reading = session.scalar(select(IntradayReading).where(
                        IntradayReading.holding_id == holding.id, IntradayReading.trade_date == day,
                        IntradayReading.data_status == "OK", IntradayReading.id != row.id)
                        .order_by(IntradayReading.id.desc()).limit(1))
                    if previous_reading and (prior_volume := (previous_reading.snapshot or {}).get("volume")) is not None:
                        if float(volume) < float(prior_volume):
                            raise UnusableQuote("当日累计成交量倒退，行情可能陈旧")
                        if (float(volume) == float(prior_volume) and
                                trading_minutes_since(datetime.fromisoformat(previous_reading.observed_at), observed) >= 60):
                            raise UnusableQuote("累计成交量持续一小时未变化，行情待复核")
            except UnusableQuote as exc:
                row.reason = str(exc)[:240]
                run.error_count += 1
                reason = f"{target.symbol} {row.reason}"
                run.error_summary = f"{run.error_summary}\n{reason}" if run.error_summary else reason
                continue
            row.price, row.quote_time, row.change_pct = prices["price"], quote.get("quote_time"), change
            row.snapshot = {**prices, "date":day, "adjustment_verified":True, "adjust":adjust,
                            "history_start":bars[0].bar_date, "previous_date":bars[-1].bar_date,
                            "previous_adjusted_close":bars[-1].close, "batch_time":quote.get("batch_time"),
                            "source_trade_date":quote.get("source_trade_date"), "volume":quote.get("volume"),
                            "time_basis":"row" if quote.get("quote_time") else "batch"}
            row.data_status, row.status = "OK", result["status"]
            row.rsi6, row.rsi12, row.rsi24 = (result[k] for k in ("rsi6", "rsi12", "rsi24"))
            evidence = {k:result[k] for k in ("prev_rsi6", "prev_rsi12", "rsi6", "rsi12", "rsi24","strategy_version")}
            evidence.update(quote_time=row.quote_time,batch_time=quote.get("batch_time"),
                            detected_at=observed.isoformat(),price=row.price,change_pct=change,
                            provider=provider,overbought=settings.overbought,
                            high_level=settings.high_level)
            record_transitions(session, holding, run_id, day, result, evidence, now)
            run.success_count += 1

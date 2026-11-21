"""Create immutable daily portfolios from one scan and the last verified daily bars."""

from datetime import datetime, timedelta

from sqlalchemy import func, select

from scripts.market_probe import SHANGHAI, is_trading_day
from .db import DailyBar, DailyBrief, Delivery, Holding, IntradayReading, Setting
from .securities import currency_for, verified_name
from .moving_averages import closing_bars, latest_averages

SITE = "https://lipume.github.io/stock-radar/"
BRIEF_TITLE = "【持仓雷达】每日持仓监控简报"
ALERT_TITLE = "🚨【持仓雷达】重点卖出风险预警"
WATCH_TITLE = "⚠️【持仓雷达】RSI 超买关注"
INDICATOR_NOTE = "相对强弱指标（RSI）用来观察近期价格动能。"
CURRENCIES = {"CNY": "人民币元", "USD": "美元", "HKD": "港元"}
SOURCE_ISSUES = {
    "REQUEST_BUDGET": "已达到本地行情请求上限，或剩余额度已预留给简报和收盘检测",
    "SOURCE_PAUSED": "行情源已暂停请求",
    "RATE_LIMIT": "行情服务商限制了请求频率",
    "AUTH": "行情服务授权无效或权限不足",
    "NETWORK": "网络连接失败，暂时无法取得行情",
    "HTTP_ERROR": "行情服务未正常响应",
    "API_ERROR": "行情服务未能完成本次查询",
    "EMPTY": "行情源尚未返回该股数据",
    "SCHEMA_CHANGED": "行情服务返回的数据格式出现变化",
    "DEPENDENCY_MISSING": "行情组件未就绪",
    "UPSTREAM_ERROR": "行情服务暂时不可用",
}

LABELS = {
    "ALERT": "重点预警",
    "WATCH": "超买关注",
    "ERROR": "数据异常",
    "NO_RULE_TRIGGERED": "未触发卖出风险规则",
}
ICONS = {"ALERT": "🚨", "WATCH": "⚠️", "ERROR": "❌", "NO_RULE_TRIGGERED": "✅"}


def notification_status(session, now: datetime) -> dict:
    day = now.astimezone(SHANGHAI).date()
    used = (
        session.scalar(
            select(func.sum(Delivery.attempts)).where(
                Delivery.created_at >= day.isoformat(),
                Delivery.created_at < (day + timedelta(days=1)).isoformat(),
            )
        )
        or 0
    )
    setting = session.get(Setting, 1)
    brief = session.scalar(select(DailyBrief).where(DailyBrief.trade_date == day.isoformat()))
    status = "PENDING" if setting.brief_enabled else "DISABLED"
    if setting.brief_enabled and not brief:
        try:
            if not is_trading_day(day):
                status = "休市"
        except ValueError:
            status = "交易日历待更新"
    if brief:
        status = session.scalar(select(Delivery.status).where(Delivery.run_id == brief.run_id)) or "PENDING"
    return {
        "used": used,
        "limit": setting.message_daily_limit,
        "remaining": max(0, setting.message_daily_limit - used),
        "brief_status": status,
    }


def readable_reason(reason):
    causes = [message for code, message in SOURCE_ISSUES.items() if code in reason]
    if causes:
        prefix = "复权口径未核实，" if "复权口径未核实" in reason else ""
        return prefix + "，".join(causes)
    if "盘中行情获取失败 (" in reason:
        return "盘中行情获取失败"
    return reason.replace("OHLC", "开盘价、最高价、最低价和收盘价")


def _item(session, holding, reading, day):
    symbol = holding.symbol
    name = (reading.name if reading else None) or verified_name(symbol)
    currency = currency_for(symbol, holding.market)
    valid = bool(
        reading
        and reading.data_status == "OK"
        and reading.trade_date == day
        and reading.status in LABELS
        and reading.status != "ERROR"
        and reading.price is not None
        and reading.quote_time
        and all(getattr(reading, k) is not None for k in ("rsi6", "rsi12", "rsi24"))
    )
    if valid:
        return {
            "symbol": symbol,
            "name": name,
            "currency": reading.currency or currency,
            "status": reading.status,
            "phase": "PROVISIONAL",
            **latest_averages(session, symbol, holding.market, day=day, provisional_price=reading.price),
            "price": reading.price,
            "quote_time": reading.quote_time,
            "change_pct": reading.change_pct,
            "rsi6": reading.rsi6,
            "rsi12": reading.rsi12,
            "rsi24": reading.rsi24,
            "reason": None,
        }
    bar = session.scalar(
        closing_bars(symbol, holding.market)
        .where(DailyBar.bar_date <= day, DailyBar.rsi24.is_not(None))
        .limit(1)
    )
    reason = (reading.reason if reading else None) or "本轮缺少有效盘中行情"
    return {
        "symbol": symbol,
        "name": name,
        "currency": currency,
        "status": "ERROR",
        "phase": "LAST_CLOSE",
        **(latest_averages(session, symbol, holding.market, day=bar.bar_date) if bar else
           {"ma5": None, "ma10": None, "ma20": None}),
        "price": bar.close if bar else None,
        "quote_time": bar.bar_date if bar else None,
        "change_pct": None,
        "rsi6": bar.rsi6 if bar else None,
        "rsi12": bar.rsi12 if bar else None,
        "rsi24": bar.rsi24 if bar else None,
        "reason": reason,
    }


def number(value):
    return "—" if value is None else f"{value:.2f}"


def format_brief(items, now):
    counts = {status: sum(item["status"] == status for item in items) for status in LABELS}
    summary = " · ".join(f"{LABELS[k]} {n}" for k, n in counts.items())
    lines = [f"📊 持仓雷达 · {now:%Y-%m-%d %H:%M}（北京时间）", f"监控 {len(items)} 只 ｜ {summary}", INDICATOR_NOTE]
    for item in items:
        status = item["status"]
        name = item["name"] or "名称待核实"
        lines.append(f"**{ICONS[status]} {name}（{item['symbol']}） · {LABELS[status]}**")
        currency = CURRENCIES.get(item["currency"], "币种待核实")
        quote = number(item["price"]) + " " + currency
        stamp = str(item["quote_time"] or "无有效行情时间")
        rsi = " / ".join(number(item[k]) for k in ("rsi6", "rsi12", "rsi24"))
        if status == "ERROR":
            lines.append(f"{readable_reason(item['reason'])}。最近正式日线 {stamp}，前复权收盘 {quote}；今日涨跌待核实。")
        else:
            change = "—" if item["change_pct"] is None else f"{item['change_pct']:+.2f}%"
            lines.append(f"{quote} ｜ 今日 {change} ｜ 行情 {stamp}")
        lines.append(f"RSI（6 / 12 / 24 日）：{rsi}")
        averages = " / ".join(number(item.get(k)) for k in ("ma5", "ma10", "ma20"))
        lines.append(f"5 / 10 / 20 日均价（{currency}）：{averages}")
    focus = [i["symbol"] for i in items if i["status"] in ("WATCH", "ALERT")]
    conclusion = "关注 " + "、".join(focus) if focus else "本轮有效行情未触发卖出风险规则"
    if counts["ERROR"] == len(items) and items:
        conclusion = "本轮全部持仓均缺少有效盘中结果"
    if not items:
        conclusion = "当前没有启用监控的持仓"
    lines.append(f"今日小结：{conclusion}；数据异常 {counts['ERROR']} 只。")
    lines.append(f"盘中结果为暂定判断，最终 RSI 以正式收盘日线为准。\n\n[查看持仓详情]({SITE})")
    return "\n\n".join(lines)


def create_brief(session, run_id, now):
    local = now.astimezone(SHANGHAI)
    day = local.date().isoformat()
    readings = {
        r.holding_id: r
        for r in session.scalars(select(IntradayReading).where(IntradayReading.run_id == run_id))
    }
    holdings = session.scalars(
        select(Holding).where(Holding.enabled, ~Holding.deleted).order_by(Holding.symbol)
    )
    items = [_item(session, h, readings.get(h.id), day) for h in holdings]
    items.sort(key=lambda i: (tuple(LABELS).index(i["status"]), i["symbol"]))
    row = DailyBrief(
        trade_date=day,
        run_id=run_id,
        created_at=local.isoformat(),
        title=BRIEF_TITLE,
        body=format_brief(items, local),
        items=items,
    )
    session.add(row)
    return row

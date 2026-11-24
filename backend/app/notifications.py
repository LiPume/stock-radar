"""Server酱 Turbo delivery with one audited batch per scan."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from http.client import HTTPException
import json
import os
import re
import socket
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from sqlalchemy import select, text, tuple_

from scripts.market_probe import NoKeyRedirect, SHANGHAI, is_trading_day
from .briefings import ALERT_TITLE, WATCH_TITLE, INDICATOR_NOTE, SITE, create_brief, notification_status, number
from .db import CheckRun, DB, DailyBrief, Delivery, RiskTransition, Setting, SignalEvent
from .securities import verified_name


def send_message(title: str, body: str) -> tuple[str, str, str | None]:
    key = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not re.fullmatch(r"SCT[a-zA-Z0-9_-]+", key):
        return "FAILED", "CONFIG", "请配置 Server酱 Turbo SendKey"
    request = Request(
        f"https://sctapi.ftqq.com/{key}.send",
        data=json.dumps({"title": title, "desp": body}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with build_opener(NoKeyRedirect()).open(request, timeout=10) as response:
            reply = json.loads(response.read(65536))
    except HTTPError as exc:
        exc.close()
        return "FAILED", str(exc.code), "服务商拒绝推送" if exc.code < 500 else "服务商返回错误"
    except URLError as exc:
        if isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError)):
            return "FAILED", "CONNECT_FAILED", "连接服务商失败"
        return "UNKNOWN", "NETWORK", "网络响应中断，投递状态待核对"
    except (OSError, ValueError, HTTPException):
        return "UNKNOWN", "NO_RECEIPT", "未取得有效回执"
    code = reply.get("code") if isinstance(reply, dict) else None
    if type(code) is not int:
        return "UNKNOWN", "NO_CODE", "回执缺少业务码"
    if code:
        return "FAILED", str(code), "服务商返回非零业务码"
    return "SENT", "0", None


def _previously_announced(session, day):
    deliveries = session.scalars(
        select(Delivery).where(
            Delivery.created_at >= day, Delivery.status.in_(("SENT", "SENDING", "UNKNOWN"))
        )
    )
    return {i: datetime.fromisoformat(d.created_at) for d in deliveries for i in d.lifecycle_ids or []}


def _select_events(session, run_id, now, config):
    transitions = session.scalars(
        select(RiskTransition).where(
            RiskTransition.run_id == run_id, RiskTransition.stage.in_(("PROVISIONAL", "CONFIRMED"))
        )
    ).all()
    signals = session.scalars(select(SignalEvent).where(SignalEvent.run_id == run_id)).all()
    keys = {(r.holding_id, r.trade_date, r.rule_code) for r in transitions}
    events = list(transitions)
    events.extend(s for s in signals if (s.holding_id, s.bar_date, s.rule_code) not in keys)
    if not events:
        return [], [], [], None
    today = now.date().isoformat()
    seen = _previously_announced(session, today)
    earlier = session.scalars(select(RiskTransition).where(RiskTransition.id.in_(seen))).all() if seen else []
    recent = {(r.holding_id, r.rule_code) for r in earlier if now - seen[r.id] < timedelta(minutes=60)}
    historical = (SignalEvent.holding_id, SignalEvent.bar_date, SignalEvent.rule_code)
    closed = set(
        session.execute(
            select(*historical).where(
                SignalEvent.run_id < run_id,
                tuple_(*historical).in_([(s.holding_id, s.bar_date, s.rule_code) for s in signals]),
            )
        )
    )
    chosen, reasons = [], []
    for event in events:
        stage = getattr(event, "stage", "CONFIRMED")
        day = event.trade_date if isinstance(event, RiskTransition) else event.bar_date
        if day != today:
            reasons.append("仅投递当日信号")
        elif stage == "CONFIRMED" and (
            getattr(event, "parent_id", None) in seen or (event.holding_id, day, event.rule_code) in closed
        ):
            reasons.append("同一风险已通知或记录，收盘结果已归档")
        elif stage == "PROVISIONAL" and (event.holding_id, event.rule_code) in recent:
            reasons.append("同一风险再次出现，60 分钟发送冷却中")
        elif event.severity == "ALERT" and not config.urgent_enabled:
            reasons.append("重点预警通知已关闭")
        elif event.severity == "WATCH" and not config.watch_enabled:
            reasons.append("普通关注通知已关闭")
        else:
            chosen.append(event)
    chosen.sort(key=lambda e: (e.severity != "ALERT", e.symbol))
    return chosen, list(signals), list(transitions), "；".join(dict.fromkeys(reasons)) or None


def _evidence(event, name=None):
    values = event.evidence
    symbol = event.symbol
    name = name or verified_name(symbol)
    label = symbol if not name else f"{name}（{symbol}）"
    phase = "盘中暂定" if getattr(event, "stage", None) == "PROVISIONAL" else "收盘确认"
    quote = ""
    if values.get("price") is not None:
        quote = f"\n检测时间：{values.get('detected_at', '—')}；最新价格：{number(values['price'])}；今日涨跌：{number(values.get('change_pct'))}%"
    pair6 = f"RSI6 {number(values.get('prev_rsi6'))} → {number(values.get('rsi6'))}"
    if event.severity == "ALERT":
        pair12 = f"RSI12 {number(values.get('prev_rsi12'))} → {number(values.get('rsi12'))}"
        return f"{label} ｜ {phase} · 高位死叉{quote}\n{pair6}；{pair12}；RSI24 {number(values.get('rsi24'))}。RSI6 从高位下穿 RSI12，请评估持仓风险。"
    return f"{label} ｜ {phase} · 超买关注{quote}\n{pair6}，上破 {number(values.get('overbought', 80))}。"


def _covered_in_brief(session, brief):
    state_by_symbol = {i["symbol"]: i["status"] for i in brief.items if i["status"] in ("ALERT", "WATCH")}
    latest = {}
    if state_by_symbol:
        for event in session.scalars(
            select(RiskTransition)
            .where(RiskTransition.trade_date == brief.trade_date, RiskTransition.symbol.in_(state_by_symbol))
            .order_by(RiskTransition.id)
        ):
            latest[(event.symbol, event.rule_code)] = event
    return {
        event.id
        for event in latest.values()
        if event.stage == "PROVISIONAL" and state_by_symbol[event.symbol] == event.severity
    }


def _budget(session, now, kind):
    status = notification_status(session, now)
    remaining = status["remaining"]
    if remaining == 0:
        return 0, "本地每日通知请求额度已用尽"
    if kind != "brief" and status["brief_status"] == "PENDING":
        remaining -= 1
        if remaining <= 0:
            return 0, "剩余额度预留给每日简报"
    cfg = session.get(Setting, 1)
    if kind == "watch" and cfg.urgent_enabled and now.strftime("%H:%M") < cfg.brief_time:
        remaining -= 1
        if remaining <= 0:
            return 0, "剩余额度优先用于重点预警"
    return remaining, None


def _claim(db, run_id, now, daily_brief):
    with db.sessions.begin() as session:
        # Serialize the daily quota check, the dated brief and send intent in SQLite.
        session.execute(text("BEGIN IMMEDIATE"))
        if session.scalar(select(Delivery.id).where(Delivery.run_id == run_id)):
            return None
        config = session.get(Setting, 1)
        chosen, signals, transitions, excluded = _select_events(session, run_id, now, config)
        brief = None
        if daily_brief and config.brief_enabled:
            started = datetime.fromisoformat(session.get(CheckRun, run_id).started_at).astimezone(SHANGHAI)
            try:
                trading = is_trading_day(started.date())
            except ValueError:
                trading = False
            if (
                trading
                and started.date() == now.date()
                and not session.scalar(
                    select(DailyBrief.id).where(DailyBrief.trade_date == started.date().isoformat())
                )
            ):
                brief = create_brief(session, run_id, started)
        if not brief and not transitions and not signals:
            return None
        eligible = [e.id for e in chosen if isinstance(e, RiskTransition)]
        if brief:
            kind, title = "brief", brief.title
            eligible = sorted(set(eligible) | _covered_in_brief(session, brief))
            names = {i["symbol"]: i["name"] for i in brief.items}
            if chosen:
                risk_lines = "\n\n".join(_evidence(e, names.get(e.symbol)) for e in chosen)
                brief.body = f"本轮新触发风险\n\n{INDICATOR_NOTE}\n\n{risk_lines}\n\n" + brief.body.replace(INDICATOR_NOTE + "\n\n", "", 1)
            body = brief.body
        else:
            kind = (
                "alert" if any(e.severity == "ALERT" for e in chosen or transitions or signals) else "watch"
            )
            title = ALERT_TITLE if kind == "alert" else WATCH_TITLE
            lines = [f"检测时间：{now:%Y-%m-%d %H:%M}（北京时间）", INDICATOR_NOTE]
            lines.extend(_evidence(e) for e in chosen)
            lines.append(f"请结合持仓计划评估风险。[查看持仓雷达]({SITE})")
            body = "\n\n".join(lines)
        row = Delivery(
            run_id=run_id,
            event_ids=[s.id for s in signals],
            lifecycle_ids=eligible,
            kind=kind,
            title=title,
            body=body,
            status="SKIPPED",
            attempts=0,
            created_at=now.isoformat(),
        )
        session.add(row)
        if not config.notifications_enabled:
            row.reason = "微信通知已关闭"
        elif not os.environ.get("SERVERCHAN_SENDKEY", "").strip():
            row.reason = "未配置 Server酱 SendKey"
        elif not brief and not chosen:
            row.reason = excluded or "无新信号"
        else:
            available, reason = _budget(session, now, kind)
            if not available:
                row.reason = reason
            else:
                row.status, row.attempts = "SENDING", 1
                row.reason = excluded
                return title, body
        return None


def deliver_batch(db: DB, run_id: int, now: datetime, *, sender=send_message, daily_brief=False) -> None:
    now = now.astimezone(SHANGHAI)
    payload = _claim(db, run_id, now, daily_brief)
    if payload is None:
        return
    for attempt in range(2):
        if attempt:
            sleep(1)
        try:
            result, code, reason = sender(*payload)
        except Exception:
            result, code, reason = "UNKNOWN", "NO_RECEIPT", "发送中断，投递回执待核对"
        with db.sessions.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(select(Delivery).where(Delivery.run_id == run_id))
            if row.status != "SENDING":
                return
            row.response_code = code
            if not attempt and result == "FAILED" and code == "CONNECT_FAILED":
                available, limit_reason = _budget(session, now, row.kind)
                if available:
                    row.attempts += 1
                    continue
                reason = f"{reason or '连接失败'}；{limit_reason}"
            row.status = result
            if reason:
                row.reason = reason
            return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="独立通道测试，不读取或修改持仓")
    parser.add_argument("--send-test", action="store_true", help="向已配置的真实通道发送一条测试消息")
    if parser.parse_args().send_test:
        status, code, reason = send_message("持仓雷达 · 通道测试", "这是一条模拟通知，仅验证微信通道。")
        print(status, code, reason or "")
        raise SystemExit(0 if status == "SENT" else 1)
    print("模拟通知预览：持仓雷达 · 通道测试（仅验证微信通道）")

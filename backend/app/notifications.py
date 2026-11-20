"""Server酱 Turbo delivery with one audited batch per scan."""
from __future__ import annotations

import argparse
from datetime import datetime
from http.client import HTTPException
import json
import os
import re
import socket
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from sqlalchemy import func, select, tuple_

from scripts.market_probe import NoKeyRedirect, SHANGHAI
from .db import DB, Delivery, Setting, SignalEvent


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


def deliver_batch(db: DB, run_id: int, now: datetime, *, sender=send_message) -> None:
    with db.sessions() as session:
        events = session.scalars(select(SignalEvent).where(SignalEvent.run_id == run_id)
                                 .order_by(SignalEvent.id)).all()
        if not events or session.scalar(select(Delivery.id).where(Delivery.run_id == run_id)):
            return
        setting = session.get(Setting, 1)
        today = now.astimezone(SHANGHAI).date().isoformat()
        # Version changes may generate fresh audit rows for an already observed
        # crossing. Group notifications by the actual holding/date/rule occurrence.
        occurrence = (SignalEvent.holding_id, SignalEvent.bar_date, SignalEvent.rule_code)
        keys = [(event.holding_id, event.bar_date, event.rule_code) for event in events]
        previous = set(session.execute(select(*occurrence).where(
            SignalEvent.run_id < run_id, tuple_(*occurrence).in_(keys))))
        fresh = [event for event, key in zip(events, keys) if key not in previous]
        used = session.scalar(select(func.sum(Delivery.attempts)).where(Delivery.created_at >= today)) or 0
        row = Delivery(run_id=run_id, event_ids=[event.id for event in (fresh or events)], attempts=0,
                       created_at=now.isoformat(), status="SKIPPED")
        session.add(row)
        if not fresh:
            row.reason = "同日同一规则已记录"
        elif not setting.notifications_enabled:
            row.reason = "微信通知已关闭"
        elif any(event.bar_date != today for event in events):
            row.reason = "仅投递当日收盘事件"
        elif not os.environ.get("SERVERCHAN_SENDKEY", "").strip():
            row.reason = "未配置 Server酱 SendKey"
        elif used >= 5:
            row.reason = "本地每日 5 次请求额度已用尽"
        else:
            row.status = "SENDING"
        session.commit()
        if row.status == "SKIPPED":
            return
        lines = []
        for event in fresh:
            label = "高位死叉 · 重点预警" if event.rule_code == "RSI_HIGH_DEATH_CROSS" else "超买上破 · 关注"
            lines.append(f"{event.symbol}　{label}　RSI6 {event.evidence['rsi6']:.2f}")
        body = f"{today} 收盘监测\n\n" + "\n\n".join(lines)
        for attempt in range(min(2, 5 - used)):
            if attempt:
                sleep(1)
            row.attempts += 1
            row.status = "SENDING"
            session.commit()  # Persist intent before any external side effect.
            row.status, row.response_code, row.reason = sender("持仓雷达 · 新风险信号", body)
            session.commit()
            if row.response_code != "CONNECT_FAILED":
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="独立通道测试，不读取或修改持仓")
    parser.add_argument("--send-test", action="store_true", help="向已配置的真实通道发送一条测试消息")
    if parser.parse_args().send_test:
        status, code, reason = send_message("持仓雷达 · 通道测试", "这是一条模拟通知，仅验证微信通道。")
        print(status, code, reason or "")
        raise SystemExit(0 if status == "SENT" else 1)
    print("模拟通知预览：持仓雷达 · 通道测试（仅验证微信通道）")

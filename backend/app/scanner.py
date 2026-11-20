"""Daily end-of-session scanner for the single-worker FastAPI deployment."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import fcntl
import os
from pathlib import Path
from threading import Lock

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert

from scripts import market_probe as probe
from stock_radar.indicators.rsi import calculate_rsi_bundle
from stock_radar.rules.engine import evaluate_history
from .db import CheckRun, DB, DailyBar, Delivery, Holding, Setting, SignalEvent
from .notifications import deliver_batch, send_message
from .providers import fetch_history
from .scheduler import make_scheduler


class ScanBusy(ValueError):
    pass


class ScanCooldown(ValueError):
    pass


class ScanService:
    def __init__(self, db: DB, *, fetch=fetch_history, sender=send_message, clock=probe.current_time):
        self.db = db
        self.fetch = fetch
        self.sender = sender
        self.clock = clock
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-scan")
        self.gate = Lock()
        self.scheduler = None
        self.owner = None

    def start(self) -> None:
        filename = self.db.engine.url.database
        if filename and filename != ":memory:":
            self.owner = Path(filename + ".scan.lock").open("a")
            try:
                fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.owner.close()
                self.owner = None
                raise RuntimeError("数据库已有运行中的服务，请使用单个 worker") from None
        try:
            with self.db.sessions.begin() as session:
                interrupted = session.scalars(select(CheckRun.id).where(CheckRun.state == "RUNNING")).all()
                for run_id in interrupted:
                    event_ids = session.scalars(select(SignalEvent.id).where(SignalEvent.run_id == run_id)).all()
                    if event_ids and not session.scalar(select(Delivery.id).where(Delivery.run_id == run_id)):
                        session.add(Delivery(run_id=run_id, event_ids=list(event_ids), status="SKIPPED",
                                             reason="任务中断，未发起通知"))
                session.execute(update(CheckRun).where(CheckRun.state == "RUNNING")
                                .values(state="FAILED", finished_at=self.clock().isoformat(),
                                        error_summary="上次服务中断"))
                session.execute(update(Delivery).where(Delivery.status == "SENDING")
                                .values(status="UNKNOWN", reason="服务中断，投递回执待核对"))
            if os.getenv("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes", "on"):
                self.scheduler = make_scheduler(self)
                self.scheduler.start()
                now = self.clock()
                if (now.hour, now.minute) >= (15, 45) and now.weekday() < 5:
                    self.scheduled_tick("scheduled")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        self.pool.shutdown(wait=True)
        if self.owner:
            self.owner.close()

    def scheduled_tick(self, trigger_type: str) -> None:
        now = self.clock()
        try:
            if probe.latest_closed_session(now) != now.date():
                return
        except ValueError:
            # Record a failed scan with the uncovered calendar year for the operator.
            pass
        today = now.date().isoformat()
        with self.db.sessions() as session:
            if session.scalar(select(CheckRun.id).where(CheckRun.trigger_type == trigger_type,
                                                        CheckRun.started_at >= today)):
                return
            if trigger_type.startswith("retry"):
                if not session.scalar(select(Holding.id).where(Holding.data_status == "STALE",
                                                               Holding.enabled, ~Holding.deleted)):
                    return
        try:
            self.trigger(trigger_type)
        except (ScanBusy, ScanCooldown):
            pass  # Another scan is already evaluating holdings.

    def trigger(self, trigger_type: str = "manual") -> int:
        if not self.gate.acquire(blocking=False):
            raise ScanBusy("已有检测任务正在运行")
        try:
            now = self.clock()
            with self.db.sessions.begin() as session:
                last = session.scalar(select(CheckRun).where(CheckRun.trigger_type == "manual")
                                      .order_by(CheckRun.id.desc()).limit(1))
                if (trigger_type == "manual" and last and
                        now - datetime.fromisoformat(last.started_at) < timedelta(seconds=30)):
                    raise ScanCooldown("手动检测间隔为 30 秒")
                run = CheckRun(trigger_type=trigger_type, started_at=now.isoformat(), state="RUNNING")
                session.add(run)
                session.flush()
                run_id = run.id
            self.pool.submit(self._worker, run_id, trigger_type, now)
            return run_id
        except BaseException:
            self.gate.release()
            raise

    def _worker(self, run_id: int, trigger_type: str, now: datetime) -> None:
        error = None
        try:
            self._scan(run_id, trigger_type, now)
            deliver_batch(self.db, run_id, self.clock(), sender=self.sender)
        except Exception as exc:
            # Library exceptions may contain secret URLs. Only store safe diagnostics.
            error = f"任务中断 ({type(exc).__name__})"
        finally:
            try:
                with self.db.sessions.begin() as session:
                    run = session.get(CheckRun, run_id)
                    if error:
                        run.state, run.error_summary = "FAILED", error
                    elif run.error_count:
                        run.state = "PARTIAL" if run.success_count else "FAILED"
                    else:
                        run.state = "SUCCESS"
                    run.finished_at = self.clock().isoformat()
            finally:
                self.gate.release()

    def _scan(self, run_id: int, trigger_type: str, now: datetime) -> None:
        with self.db.sessions() as session:
            query = select(Holding).where(Holding.enabled, ~Holding.deleted).order_by(Holding.id)
            if trigger_type.startswith("retry"):
                query = query.where(Holding.data_status == "STALE")
            stocks = {h.symbol: h for h in session.scalars(query)}
            settings = session.get(Setting, 1)
        try:
            end = probe.latest_closed_session(now)
        except ValueError:
            for holding in stocks.values():
                self._save_error(run_id, holding.id, "CALENDAR: 交易日历未覆盖当前年份")
            if not stocks:
                with self.db.sessions.begin() as session:
                    run = session.get(CheckRun, run_id)
                    run.error_count, run.error_summary = 1, "交易日历未覆盖当前年份"
            return
        start = end - timedelta(days=240)
        pending = []
        for holding in stocks.values():
            cached = self._cached(holding, now, start, end)
            if cached:
                self._process(run_id, holding, cached, None, start, end, settings, holding.fetched_at, True)
            else:
                pending.append((holding.symbol, holding.market))
        remaining = dict(pending)
        try:
            for symbol, rows, error in self.fetch(pending, start, end):
                if symbol not in remaining:
                    continue
                self._process(run_id, stocks[symbol], rows, error, start, end, settings, self.clock().isoformat(), False)
                del remaining[symbol]
            missing = "SYMBOL_MISSING: 数据源未返回该标的"
        except Exception as exc:
            missing = f"UPSTREAM_ERROR: {type(exc).__name__}"
        for symbol in remaining:
            self._save_error(run_id, stocks[symbol].id, missing)

    def _cached(self, holding: Holding, now: datetime, start: date, end: date) -> list[dict]:
        if (holding.data_status != "OK" or holding.bar_date != end.isoformat()
                or not holding.fetched_at or (holding.market == "A" and not os.getenv("HITHINK_FINANCE_API_KEY"))):
            return []
        age = now - datetime.fromisoformat(holding.fetched_at)
        if age < timedelta(0) or age >= timedelta(minutes=5):
            return []
        with self.db.sessions() as session:
            query = select(DailyBar).where(DailyBar.symbol == holding.symbol, DailyBar.bar_date >= start.isoformat(),
                                          DailyBar.provider == holding.provider).order_by(DailyBar.bar_date)
            return [{"date": bar.bar_date, **{key: getattr(bar, key) for key in probe.BAR_FIELDS[1:]}}
                    for bar in session.scalars(query)]

    def _save_error(self, run_id: int, holding_id: int, reason: str) -> None:
        category = reason.split(":")[0]
        status = category if category in ("STALE", "INSUFFICIENT_DATA") else "DATA_UNAVAILABLE"
        with self.db.sessions.begin() as session:
            holding = session.get(Holding, holding_id)
            if holding.deleted or not holding.enabled:
                return
            holding.data_status = holding.status = status
            holding.reason = reason
            holding.provider = "hithink" if holding.market == "A" else "akshare-b"
            holding.rsi6 = holding.rsi12 = holding.rsi24 = None
            run = session.get(CheckRun, run_id)
            run.error_count += 1
            line = f"{holding.symbol} {reason}"
            run.error_summary = f"{run.error_summary}\n{line}" if run.error_summary else line

    def _process(self, run_id: int, target: Holding, rows: list[dict], error: str | None,
                 start: date, end: date, settings: Setting, fetched_at: str, cached: bool) -> None:
        provider, adjust = ("hithink", "forward") if target.market == "A" else ("akshare-b", "qfq")
        if not error:
            quality = probe.check_bars(target.symbol, rows, provider, 100, expected_date=end, start_date=start)
            error = quality["reason"]
            if not error:
                # A missing previous close (e.g. a suspension) cannot establish
                # a crossing between the two most recent exchange sessions.
                prior = probe.latest_closed_session(datetime.combine(end, datetime.min.time(), probe.SHANGHAI))
                if rows[-2]["date"] != prior.isoformat():
                    error = "INSUFFICIENT_DATA: 缺少上一个交易日的收盘价"
                elif any(day.year == 2026 and day.month == m and a <= day.day <= b
                         for day in (date.fromisoformat(row["date"]) for row in rows)
                         for m, a, b in probe.HOLIDAYS_2026):
                    error = "INVALID_BARS: 收盘序列包含休市日"
        if error:
            self._save_error(run_id, target.id, error)
            return
        closes = [row["close"] for row in rows]
        evaluation = evaluate_history(closes, overbought=settings.overbought, high_level=settings.high_level)
        with self.db.sessions.begin() as session:
            holding = session.get(Holding, target.id)
            if holding.deleted or not holding.enabled:
                return
            if not cached:
                # Replace the fetched window atomically: forward adjustments can revise old prices.
                session.execute(delete(DailyBar).where(DailyBar.symbol == holding.symbol,
                                                       DailyBar.provider == provider, DailyBar.adjust == adjust))
                bundle = calculate_rsi_bundle(closes)
                session.execute(insert(DailyBar), [
                    {"symbol": holding.symbol, "bar_date": row["date"], "adjust": adjust,
                     "provider": provider, "fetched_at": fetched_at,
                     **{key: row[key] for key in probe.BAR_FIELDS[1:]},
                     **{key: series[i] for key, series in bundle.items()}}
                    for i, row in enumerate(rows)])
            holding.provider, holding.bar_date, holding.fetched_at = provider, end.isoformat(), fetched_at
            holding.data_status, holding.status, holding.reason = "OK", evaluation["status"], None
            for key in ("rsi6", "rsi12", "rsi24"):
                setattr(holding, key, evaluation[key])
            evidence = {key: evaluation[key] for key in ("prev_rsi6", "prev_rsi12", "rsi6", "rsi12", "rsi24")}
            evidence.update(provider=provider, adjust=adjust, overbought=settings.overbought, high_level=settings.high_level)
            for rule in evaluation["events"]:
                stmt = insert(SignalEvent).values(
                    holding_id=holding.id, symbol=holding.symbol, bar_date=end.isoformat(), rule_code=rule,
                    severity="ALERT" if rule == "RSI_HIGH_DEATH_CROSS" else "WATCH",
                    strategy_version=evaluation["strategy_version"], evidence=evidence, run_id=run_id,
                ).on_conflict_do_nothing(index_elements=["holding_id", "bar_date", "rule_code", "strategy_version"])
                session.execute(stmt)
            session.get(CheckRun, run_id).success_count += 1

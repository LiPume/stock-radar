"""SQLite storage shared by the HTTP service and scanner."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import ForeignKey, Index, JSON, UniqueConstraint, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Holding(Base):
    __tablename__ = "holdings"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str] = mapped_column(default="")
    market: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(default="")
    created_at: Mapped[str] = mapped_column(default=timestamp)
    data_status: Mapped[str] = mapped_column(default="PENDING")
    status: Mapped[str] = mapped_column(default="PENDING")
    reason: Mapped[str | None]
    provider: Mapped[str | None]
    bar_date: Mapped[str | None]
    fetched_at: Mapped[str | None]
    rsi6: Mapped[float | None]
    rsi12: Mapped[float | None]
    rsi24: Mapped[float | None]


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (UniqueConstraint("symbol", "bar_date", "provider", "adjust"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    bar_date: Mapped[str]
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    volume: Mapped[float]
    provider: Mapped[str]
    adjust: Mapped[str]
    fetched_at: Mapped[str]
    rsi6: Mapped[float | None]
    rsi12: Mapped[float | None]
    rsi24: Mapped[float | None]


class CheckRun(Base):
    __tablename__ = "check_runs"
    __table_args__ = (Index("uq_check_runs_slot_key", "slot_key", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_type: Mapped[str]
    state: Mapped[str] = mapped_column(default="RUNNING")
    started_at: Mapped[str] = mapped_column(default=timestamp)
    finished_at: Mapped[str | None]
    slot_key: Mapped[str | None]
    success_count: Mapped[int] = mapped_column(default=0)
    error_count: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str | None]


class SignalEvent(Base):
    __tablename__ = "signal_events"
    __table_args__ = (UniqueConstraint("holding_id", "bar_date", "rule_code", "strategy_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id"))
    symbol: Mapped[str]
    bar_date: Mapped[str]
    rule_code: Mapped[str]
    severity: Mapped[str]
    strategy_version: Mapped[str]
    evidence: Mapped[dict] = mapped_column(JSON)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"))
    created_at: Mapped[str] = mapped_column(default=timestamp)


class Delivery(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), unique=True)
    kind: Mapped[str] = mapped_column(default="close")
    title: Mapped[str] = mapped_column(default="")
    body: Mapped[str] = mapped_column(default="")
    lifecycle_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    event_ids: Mapped[list[int]] = mapped_column(JSON)
    status: Mapped[str]
    attempts: Mapped[int] = mapped_column(default=0)
    response_code: Mapped[str | None]
    reason: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=timestamp)


class Setting(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    overbought: Mapped[float] = mapped_column(default=80)
    high_level: Mapped[float] = mapped_column(default=70)
    notifications_enabled: Mapped[bool] = mapped_column(default=False)
    intraday_enabled: Mapped[bool] = mapped_column(default=False)
    interval_minutes: Mapped[int] = mapped_column(default=30)
    brief_enabled: Mapped[bool] = mapped_column(default=True)
    brief_time: Mapped[str] = mapped_column(default="14:30")
    urgent_enabled: Mapped[bool] = mapped_column(default=True)
    watch_enabled: Mapped[bool] = mapped_column(default=True)
    message_daily_limit: Mapped[int] = mapped_column(default=5)
    api_daily_limit: Mapped[int] = mapped_column(default=30)


class IntradayReading(Base):
    """One audited observation per scan. DailyBar contains official closes only."""
    __tablename__ = "intraday_readings"
    __table_args__ = (UniqueConstraint("run_id", "holding_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"))
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id"), index=True)
    symbol: Mapped[str]
    trade_date: Mapped[str] = mapped_column(index=True)
    observed_at: Mapped[str]
    quote_time: Mapped[str | None]
    provider: Mapped[str]
    name: Mapped[str | None]
    currency: Mapped[str | None]
    price: Mapped[float | None]
    change_pct: Mapped[float | None]
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    rsi6: Mapped[float | None]
    rsi12: Mapped[float | None]
    rsi24: Mapped[float | None]
    status: Mapped[str]
    data_status: Mapped[str]
    reason: Mapped[str | None]


class RiskTransition(Base):
    __tablename__ = "risk_transitions"
    __table_args__ = (UniqueConstraint("run_id", "holding_id", "rule_code", "stage"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"))
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id"), index=True)
    symbol: Mapped[str]
    trade_date: Mapped[str] = mapped_column(index=True)
    rule_code: Mapped[str]
    severity: Mapped[str]
    stage: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("risk_transitions.id"))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=timestamp)


class DailyBrief(Base):
    __tablename__ = "daily_briefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[str] = mapped_column(unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_runs.id"), unique=True)
    created_at: Mapped[str] = mapped_column(default=timestamp)
    title: Mapped[str]
    body: Mapped[str]
    items: Mapped[list] = mapped_column(JSON)


class DB:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite":
            raise ValueError("DATABASE_URL must identify a SQLite database")
        options = {}
        if parsed.database in (None, "", ":memory:"):
            options["poolclass"] = StaticPool
        else:
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15}, **options)
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")

    def migrate(self) -> None:
        from . import market_usage  # Register provider ledger/cache in application metadata.

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        with self.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

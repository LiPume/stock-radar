"""SQLite storage shared by the HTTP service and scanner."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import ForeignKey, JSON, UniqueConstraint, create_engine, event
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
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_type: Mapped[str]
    state: Mapped[str] = mapped_column(default="RUNNING")
    started_at: Mapped[str] = mapped_column(default=timestamp)
    finished_at: Mapped[str | None]
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
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        with self.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

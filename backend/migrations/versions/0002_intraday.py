"""Add intraday observations, signal transitions and daily briefs without rewriting V1 tables."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"

SETTINGS = {
    "intraday_enabled": (sa.Boolean(), "0"), "interval_minutes": (sa.Integer(), "30"),
    "brief_enabled": (sa.Boolean(), "1"), "brief_time": (sa.String(), "14:30"),
    "urgent_enabled": (sa.Boolean(), "1"), "watch_enabled": (sa.Boolean(), "1"),
    "message_daily_limit": (sa.Integer(), "5"), "api_daily_limit": (sa.Integer(), "30"),
}
DELIVERY = {"kind": (sa.String(), "close"), "title": (sa.String(), ""), "body": (sa.String(), ""),
            "lifecycle_ids": (sa.JSON(), "[]")}


def upgrade():
    for table, columns in (("settings", SETTINGS), ("notification_deliveries", DELIVERY)):
        for name, (kind, default) in columns.items():
            op.add_column(table, sa.Column(name, kind, nullable=False, server_default=default))
    op.add_column("check_runs", sa.Column("slot_key", sa.String()))
    op.create_index("uq_check_runs_slot_key", "check_runs", ["slot_key"], unique=True)
    op.create_table("intraday_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("check_runs.id"), nullable=False),
        sa.Column("holding_id", sa.Integer(), sa.ForeignKey("holdings.id"), nullable=False, index=True),
        sa.Column("symbol", sa.String(), nullable=False), sa.Column("trade_date", sa.String(), nullable=False, index=True),
        sa.Column("observed_at", sa.String(), nullable=False), sa.Column("quote_time", sa.String()),
        sa.Column("provider", sa.String(), nullable=False), sa.Column("name", sa.String()), sa.Column("currency", sa.String()),
        sa.Column("price", sa.Float()), sa.Column("change_pct", sa.Float()), sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("rsi6", sa.Float()), sa.Column("rsi12", sa.Float()), sa.Column("rsi24", sa.Float()),
        sa.Column("status", sa.String(), nullable=False), sa.Column("data_status", sa.String(), nullable=False),
        sa.Column("reason", sa.String()), sa.UniqueConstraint("run_id", "holding_id"))
    op.create_table("risk_transitions",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("run_id", sa.Integer(), sa.ForeignKey("check_runs.id"), nullable=False),
        sa.Column("holding_id", sa.Integer(), sa.ForeignKey("holdings.id"), nullable=False, index=True),
        sa.Column("symbol", sa.String(), nullable=False), sa.Column("trade_date", sa.String(), nullable=False, index=True),
        sa.Column("rule_code", sa.String(), nullable=False), sa.Column("severity", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False), sa.Column("parent_id", sa.Integer(), sa.ForeignKey("risk_transitions.id")),
        sa.Column("evidence", sa.JSON(), nullable=False), sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("run_id", "holding_id", "rule_code", "stage"))
    op.create_table("daily_briefs", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_date", sa.String(), nullable=False, unique=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("check_runs.id"), nullable=False, unique=True),
        sa.Column("created_at", sa.String(), nullable=False), sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False), sa.Column("items", sa.JSON(), nullable=False))


def downgrade():
    for table in ("daily_briefs", "risk_transitions", "intraday_readings"):
        op.drop_table(table)
    op.drop_index("uq_check_runs_slot_key", table_name="check_runs")
    op.drop_column("check_runs", "slot_key")
    for table, columns in (("notification_deliveries", DELIVERY), ("settings", SETTINGS)):
        for name in columns:
            op.drop_column(table, name)

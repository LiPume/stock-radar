"""Persist physical market requests, logical operations, cooldowns and cached batches."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"

def upgrade():
    op.create_table("market_usage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String, nullable=False), sa.Column("at", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False), sa.Column("operation", sa.String, nullable=False),
        sa.Column("symbols", sa.Integer, nullable=False), sa.Column("records", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False), sa.Column("elapsed_ms", sa.Integer, nullable=False),
        sa.Column("pause_until", sa.String))
    op.create_index("ix_market_provider_time", "market_usage", ["provider","at"])
    op.create_table("market_cache", sa.Column("cache_key", sa.String, primary_key=True),
        sa.Column("at", sa.String, nullable=False), sa.Column("payload", sa.Text, nullable=False))

def downgrade():
    op.drop_table("market_cache")
    op.drop_index("ix_market_provider_time",table_name="market_usage")
    op.drop_table("market_usage")

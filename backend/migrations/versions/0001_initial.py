"""Initial holdings, price, event, check and delivery schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table('check_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trigger_type', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('started_at', sa.String(), nullable=False),
    sa.Column('finished_at', sa.String(), nullable=True),
    sa.Column('success_count', sa.Integer(), nullable=False),
    sa.Column('error_count', sa.Integer(), nullable=False),
    sa.Column('error_summary', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('daily_bars',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('bar_date', sa.String(), nullable=False),
    sa.Column('open', sa.Float(), nullable=False),
    sa.Column('high', sa.Float(), nullable=False),
    sa.Column('low', sa.Float(), nullable=False),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('volume', sa.Float(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('adjust', sa.String(), nullable=False),
    sa.Column('fetched_at', sa.String(), nullable=False),
    sa.Column('rsi6', sa.Float(), nullable=True),
    sa.Column('rsi12', sa.Float(), nullable=True),
    sa.Column('rsi24', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'bar_date', 'provider', 'adjust')
    )
    op.create_table('holdings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=False),
    sa.Column('market', sa.String(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.String(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('data_status', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('provider', sa.String(), nullable=True),
    sa.Column('bar_date', sa.String(), nullable=True),
    sa.Column('fetched_at', sa.String(), nullable=True),
    sa.Column('rsi6', sa.Float(), nullable=True),
    sa.Column('rsi12', sa.Float(), nullable=True),
    sa.Column('rsi24', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol')
    )
    op.create_table('settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('overbought', sa.Float(), nullable=False),
    sa.Column('high_level', sa.Float(), nullable=False),
    sa.Column('notifications_enabled', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('notification_deliveries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('event_ids', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('response_code', sa.String(), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['check_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id')
    )
    op.create_table('signal_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('holding_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(), nullable=False),
    sa.Column('bar_date', sa.String(), nullable=False),
    sa.Column('rule_code', sa.String(), nullable=False),
    sa.Column('severity', sa.String(), nullable=False),
    sa.Column('strategy_version', sa.String(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['holding_id'], ['holdings.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['check_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('holding_id', 'bar_date', 'rule_code', 'strategy_version')
    )
    op.execute("INSERT INTO settings (id, overbought, high_level, notifications_enabled) VALUES (1, 80, 70, 0)")


def downgrade():
    op.drop_table('signal_events')
    op.drop_table('notification_deliveries')
    op.drop_table('settings')
    op.drop_table('holdings')
    op.drop_table('daily_bars')
    op.drop_table('check_runs')

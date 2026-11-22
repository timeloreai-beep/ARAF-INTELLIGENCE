"""persist per-user daily paper auto-trade budget reservations

Revision ID: 20261123_004
Revises: 20261122_003
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261123_004"
down_revision: Union[str, Sequence[str], None] = "20261122_003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auto_trade_daily_budgets",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("trading_day", sa.String(length=10), nullable=False),
        sa.Column("budget_inr", sa.Numeric(38, 12), nullable=False),
        sa.Column("reserved_inr", sa.Numeric(38, 12), nullable=False),
        sa.Column("used_inr", sa.Numeric(38, 12), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "trading_day"),
    )


def downgrade() -> None:
    op.drop_table("auto_trade_daily_budgets")

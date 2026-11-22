"""Persist the timestamp when the daily-loss lock first activates."""

from alembic import op
import sqlalchemy as sa


revision = "20261126_009"
down_revision = "20261126_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_configs",
        sa.Column("daily_loss_lock_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("risk_configs", "daily_loss_lock_started_at")

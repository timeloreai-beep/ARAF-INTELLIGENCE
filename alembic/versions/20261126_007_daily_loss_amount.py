"""Use an exact INR amount for the daily loss limit."""

from alembic import op
import sqlalchemy as sa


revision = "20261126_007"
down_revision = "20261125_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_configs",
        sa.Column("max_daily_loss_amount", sa.Numeric(38, 12), nullable=True),
    )
    op.execute(
        "UPDATE risk_configs "
        "SET max_daily_loss_amount = capital_inr * max_daily_loss_pct / 100"
    )
    op.alter_column(
        "risk_configs",
        "max_daily_loss_amount",
        existing_type=sa.Numeric(38, 12),
        nullable=False,
    )
    op.drop_column("risk_configs", "max_daily_loss_pct")


def downgrade() -> None:
    op.add_column(
        "risk_configs",
        sa.Column("max_daily_loss_pct", sa.Float(), nullable=True),
    )
    op.execute(
        "UPDATE risk_configs "
        "SET max_daily_loss_pct = max_daily_loss_amount / capital_inr * 100 "
        "WHERE capital_inr > 0"
    )
    op.alter_column(
        "risk_configs",
        "max_daily_loss_pct",
        existing_type=sa.Float(),
        nullable=False,
    )
    op.drop_column("risk_configs", "max_daily_loss_amount")

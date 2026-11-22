"""Persist the 24-hour risk settings and loss-lock expiry."""

from alembic import op
import sqlalchemy as sa


revision = "20261125_006"
down_revision = "20261124_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_configs",
        sa.Column("risk_lock_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "risk_configs",
        sa.Column(
            "risk_lock_watermark_inr",
            sa.Numeric(38, 12),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "risk_configs",
        "risk_lock_watermark_inr",
        existing_type=sa.Numeric(38, 12),
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("risk_configs", "risk_lock_watermark_inr")
    op.drop_column("risk_configs", "risk_lock_expires_at")

"""Persist manual paper exits and same-setup re-entry cooldowns."""

from alembic import op
import sqlalchemy as sa


revision = "20261126_008"
down_revision = "20261126_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("exit_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "paper_positions",
        sa.Column(
            "manual_exit_blocked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "paper_positions",
        sa.Column(
            "pnl_fx_to_inr",
            sa.Numeric(38, 12),
            nullable=False,
            server_default="1",
        ),
    )
    op.alter_column(
        "paper_positions",
        "pnl_fx_to_inr",
        existing_type=sa.Numeric(38, 12),
        server_default=None,
    )
    op.add_column(
        "paper_positions",
        sa.Column("is_auto_trade", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column(
        "paper_positions",
        "is_auto_trade",
        existing_type=sa.Boolean(),
        server_default=None,
    )
    op.create_index(
        "ix_paper_positions_manual_exit_setup",
        "paper_positions",
        ["user_id", "symbol", "exchange", "side", "manual_exit_blocked_until"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_positions_manual_exit_setup", table_name="paper_positions"
    )
    op.drop_column("paper_positions", "pnl_fx_to_inr")
    op.drop_column("paper_positions", "is_auto_trade")
    op.drop_column("paper_positions", "manual_exit_blocked_until")
    op.drop_column("paper_positions", "exit_reason")

"""Add persistent paper positions for trailing and exit management."""

from alembic import op
import sqlalchemy as sa


revision = "20261124_005"
down_revision = "20261123_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("broker_token", sa.String(length=32), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("entry_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("stop_loss", sa.Numeric(38, 12), nullable=False),
        sa.Column("take_profit", sa.Numeric(38, 12), nullable=False),
        sa.Column("trailing_distance", sa.Numeric(38, 12), nullable=False),
        sa.Column("favorable_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("exit_price", sa.Numeric(38, 12), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(38, 12), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_order_id", name="uq_paper_positions_user_order"),
    )
    op.create_index("ix_paper_positions_user_id", "paper_positions", ["user_id"])
    op.create_index("ix_paper_positions_symbol", "paper_positions", ["symbol"])
    op.create_index("ix_paper_positions_exchange", "paper_positions", ["exchange"])
    op.create_index("ix_paper_positions_status", "paper_positions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_paper_positions_status", table_name="paper_positions")
    op.drop_index("ix_paper_positions_exchange", table_name="paper_positions")
    op.drop_index("ix_paper_positions_symbol", table_name="paper_positions")
    op.drop_index("ix_paper_positions_user_id", table_name="paper_positions")
    op.drop_table("paper_positions")

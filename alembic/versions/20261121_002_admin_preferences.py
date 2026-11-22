"""admin controls and synchronized terminal preferences

Revision ID: 20261121_002
Revises: 20261120_001
Create Date: 2026-11-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261121_002"
down_revision: Union[str, Sequence[str], None] = "20261120_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "admin_controls",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("auto_trade_enabled", sa.Boolean(), nullable=False),
        sa.Column("global_kill_switch", sa.Boolean(), nullable=False),
        sa.Column("kill_switch_reason", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("admin_controls")
    op.drop_table("user_preferences")

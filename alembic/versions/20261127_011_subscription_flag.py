"""Persist subscription activation flag

Revision ID: 20261127_011
Revises: 20261127_010
Create Date: 2026-11-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261127_011"
down_revision: Union[str, Sequence[str], None] = "20261127_010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_users",
        sa.Column(
            "is_subscribed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE app_users
        SET is_subscribed = TRUE
        WHERE is_vip_free = TRUE
           OR subscription_expires_at > CURRENT_TIMESTAMP
        """
    )


def downgrade() -> None:
    op.drop_column("app_users", "is_subscribed")

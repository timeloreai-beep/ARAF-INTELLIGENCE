"""Add deletion columns to app_users

Revision ID: ef512bb42a86
Revises: 20261127_012
Create Date: 2026-11-27 10:44:28.056076

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ef512bb42a86'
down_revision: Union[str, Sequence[str], None] = '20261127_012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_users",
        sa.Column("deletion_status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        "app_users",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "app_users",
        sa.Column("deletion_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "app_users",
        sa.Column("deletion_approved_by", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_app_users_deletion_status", "app_users", ["deletion_status"])


def downgrade() -> None:
    op.drop_index("ix_app_users_deletion_status", table_name="app_users")
    op.drop_column("app_users", "deletion_approved_by")
    op.drop_column("app_users", "deletion_approved_at")
    op.drop_column("app_users", "deletion_requested_at")
    op.drop_column("app_users", "deletion_status")
"""Ghost Admin user subscriptions and global pricing

Revision ID: 20261127_010
Revises: 20261126_009
Create Date: 2026-11-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261127_010"
down_revision: Union[str, Sequence[str], None] = "20261126_009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_users",
        sa.Column("is_vip_free", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "app_users",
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE app_users
        SET subscription_expires_at = kyc_records.trial_expires_at
        FROM kyc_records
        WHERE app_users.external_subject = kyc_records.user_id
        """
    )
    op.create_table(
        "system_configurations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("global_monthly_fee_inr", sa.Numeric(12, 2), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("system_configurations")
    op.drop_column("app_users", "subscription_expires_at")
    op.drop_column("app_users", "is_vip_free")

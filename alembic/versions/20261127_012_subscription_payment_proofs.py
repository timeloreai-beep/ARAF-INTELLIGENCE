"""Add persistent subscription payment proofs

Revision ID: 20261127_012
Revises: 20261127_011
Create Date: 2026-11-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261127_012"
down_revision: Union[str, Sequence[str], None] = "20261127_011"
depends_on: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscription_payment_proofs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("amount_inr", sa.Numeric(12, 2), nullable=False),
        sa.Column("proof_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("telegram_message_id", sa.String(length=64), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_payment_proofs_user_id",
        "subscription_payment_proofs",
        ["user_id"],
    )
    op.create_index(
        "ix_subscription_payment_proofs_email",
        "subscription_payment_proofs",
        ["email"],
    )
    op.create_index(
        "ix_subscription_payment_proofs_proof_sha256",
        "subscription_payment_proofs",
        ["proof_sha256"],
    )
    op.create_index(
        "ix_subscription_payment_proofs_status",
        "subscription_payment_proofs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_subscription_payment_proofs_status", table_name="subscription_payment_proofs")
    op.drop_index("ix_subscription_payment_proofs_proof_sha256", table_name="subscription_payment_proofs")
    op.drop_index("ix_subscription_payment_proofs_email", table_name="subscription_payment_proofs")
    op.drop_index("ix_subscription_payment_proofs_user_id", table_name="subscription_payment_proofs")
    op.drop_table("subscription_payment_proofs")

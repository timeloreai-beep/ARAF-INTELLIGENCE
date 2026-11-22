"""auth, KYC, and PIN persistence

Revision ID: 20261122_003
Revises: 20261121_002
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261122_003"
down_revision: Union[str, Sequence[str], None] = "20261121_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("trader_uid", sa.String(length=32), nullable=True))
    op.create_index("ix_app_users_trader_uid", "app_users", ["trader_uid"], unique=True)

    op.create_table(
        "auth_otp_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", "purpose", name="uq_auth_otp_email_purpose"),
    )
    op.create_index("ix_auth_otp_challenges_email", "auth_otp_challenges", ["email"])

    op.create_table(
        "pin_credentials",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("salt_hex", sa.String(length=64), nullable=False),
        sa.Column("pin_hash", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "kyc_records",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("trader_uid", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("pan_masked", sa.String(length=16), nullable=False),
        sa.Column("aadhaar_masked", sa.String(length=16), nullable=False),
        sa.Column("income_bracket", sa.String(length=64), nullable=False),
        sa.Column("experience", sa.String(length=64), nullable=False),
        sa.Column("authorized_segment", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("encrypted_pii", sa.Text(), nullable=False),
        sa.Column("trial_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_kyc_records_trader_uid", "kyc_records", ["trader_uid"], unique=True)
    op.create_index("ix_kyc_records_status", "kyc_records", ["status"])


def downgrade() -> None:
    op.drop_index("ix_kyc_records_status", table_name="kyc_records")
    op.drop_index("ix_kyc_records_trader_uid", table_name="kyc_records")
    op.drop_table("kyc_records")
    op.drop_table("pin_credentials")
    op.drop_index("ix_auth_otp_challenges_email", table_name="auth_otp_challenges")
    op.drop_table("auth_otp_challenges")
    op.drop_index("ix_app_users_trader_uid", table_name="app_users")
    op.drop_column("app_users", "trader_uid")

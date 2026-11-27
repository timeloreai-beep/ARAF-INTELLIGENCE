from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    external_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), default="supabase")
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    trader_uid: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user")
    kyc_status: Mapped[str] = mapped_column(String(32), default="pending")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_vip_free: Mapped[bool] = mapped_column(Boolean, default=False)
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    subscription_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deletion_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    deletion_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_approved_by: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    cancellation_window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SubscriptionPaymentProof(Base):
    __tablename__ = "subscription_payment_proofs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    months: Mapped[int] = mapped_column(Integer)
    amount_inr: Mapped[float] = mapped_column(Numeric(12, 2))
    utr: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    proof_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    telegram_message_id: Mapped[str] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class QuoteSnapshot(Base):
    __tablename__ = "quote_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(32), nullable=True)
    bid: Mapped[float] = mapped_column(Float, nullable=True)
    ask: Mapped[float] = mapped_column(Float, nullable=True)
    last_price: Mapped[float] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OrderRecord(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("user_id", "client_order_id", name="uq_orders_user_client_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    broker_order_id: Mapped[str] = mapped_column(String(128), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    operation: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Numeric(38, 12))
    entry_price: Mapped[float] = mapped_column(Numeric(38, 12), nullable=True)
    stop_loss: Mapped[float] = mapped_column(Numeric(38, 12), nullable=True)
    take_profit: Mapped[float] = mapped_column(Numeric(38, 12), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    protection_status: Mapped[str] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "exchange", name="uq_positions_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8), default="FLAT")
    quantity: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    average_price: Mapped[float] = mapped_column(Numeric(38, 12), nullable=True)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    realized_pnl: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RiskConfig(Base):
    __tablename__ = "risk_configs"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    capital_inr: Mapped[float] = mapped_column(Numeric(38, 12), default=100000)
    max_daily_loss_amount: Mapped[float] = mapped_column(Numeric(38, 12), default=2500)
    max_per_trade_risk_pct: Mapped[float] = mapped_column(Float, default=2.0)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=3)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_reason: Mapped[str] = mapped_column(String(255), nullable=True)
    risk_lock_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    risk_lock_watermark_inr: Mapped[float] = mapped_column(
        Numeric(38, 12), default=0
    )
    daily_loss_lock_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RiskState(Base):
    __tablename__ = "risk_states"
    __table_args__ = (
        UniqueConstraint("user_id", "trading_day", name="uq_risk_states_user_day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    trading_day: Mapped[str] = mapped_column(String(10), index=True)
    daily_pnl_inr: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    is_hard_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_reason: Mapped[str] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AutoTradeDailyBudget(Base):
    __tablename__ = "auto_trade_daily_budgets"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    trading_day: Mapped[str] = mapped_column(String(10), primary_key=True)
    budget_inr: Mapped[float] = mapped_column(Numeric(38, 12))
    reserved_inr: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    used_inr: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        UniqueConstraint("user_id", "client_order_id", name="uq_paper_positions_user_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(32), index=True)
    broker_token: Mapped[str] = mapped_column(String(32), nullable=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Numeric(38, 12))
    entry_price: Mapped[float] = mapped_column(Numeric(38, 12))
    stop_loss: Mapped[float] = mapped_column(Numeric(38, 12))
    take_profit: Mapped[float] = mapped_column(Numeric(38, 12))
    trailing_distance: Mapped[float] = mapped_column(Numeric(38, 12))
    favorable_price: Mapped[float] = mapped_column(Numeric(38, 12))
    pnl_fx_to_inr: Mapped[float] = mapped_column(Numeric(38, 12), default=1)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)
    is_auto_trade: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_price: Mapped[float] = mapped_column(Numeric(38, 12), nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Numeric(38, 12), default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=True)
    manual_exit_blocked_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AdminControl(Base):
    __tablename__ = "admin_controls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    auto_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    global_kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AutoTradeExecutionLog(Base):
    __tablename__ = "auto_trade_execution_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Numeric(38, 12))
    entry_price: Mapped[float] = mapped_column(Numeric(38, 12))
    stop_loss: Mapped[float] = mapped_column(Numeric(38, 12))
    take_profit: Mapped[float] = mapped_column(Numeric(38, 12))
    score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    is_auto_trade: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class SystemConfiguration(Base):
    __tablename__ = "system_configurations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    global_monthly_fee_inr: Mapped[float] = mapped_column(
        Numeric(12, 2), default=299
    )
    updated_by: Mapped[str] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AuthOtpChallenge(Base):
    __tablename__ = "auth_otp_challenges"
    __table_args__ = (
        UniqueConstraint("email", "purpose", name="uq_auth_otp_email_purpose"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PinCredential(Base):
    __tablename__ = "pin_credentials"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    salt_hex: Mapped[str] = mapped_column(String(64))
    pin_hash: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KycRecord(Base):
    __tablename__ = "kyc_records"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    trader_uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    pan_masked: Mapped[str] = mapped_column(String(16))
    aadhaar_masked: Mapped[str] = mapped_column(String(16))
    income_bracket: Mapped[str] = mapped_column(String(64), default="-")
    experience: Mapped[str] = mapped_column(String(64), default="-")
    authorized_segment: Mapped[str] = mapped_column(String(32), default="ALL")
    status: Mapped[str] = mapped_column(String(32), default="PENDING_ADMIN_REVIEW", index=True)
    encrypted_pii: Mapped[str] = mapped_column(Text)
    trial_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    actor_id: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    resource_id: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32), default="success")
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    recipient_id: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ReplicationOutbox(Base):
    __tablename__ = "replication_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    aggregate_type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(255), index=True)
    operation: Mapped[str] = mapped_column(String(32), default="upsert")
    target: Mapped[str] = mapped_column(String(32), default="backup")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class BrokerVault(Base):
    __tablename__ = "broker_vaults"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

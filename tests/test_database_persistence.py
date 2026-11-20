from datetime import datetime, timezone

from backend.app.integrations.database import (
    PersistenceStatus,
    _model_payload,
    _record_from_payload,
    normalize_async_database_url,
)
from backend.app.models import (
    AppUser,
    AuditEvent,
    Base,
    Notification,
    OrderRecord,
    Position,
    QuoteSnapshot,
    ReplicationOutbox,
    RiskConfig,
    RiskState,
)


def test_async_postgres_url_normalization():
    assert normalize_async_database_url(
        "postgresql://user:password@db.example/test?sslmode=require"
    ).startswith("postgresql+asyncpg://")


def test_persistence_status_never_claims_replication_for_partial_write():
    status = PersistenceStatus(
        primary_applied=True,
        backup_applied=False,
        replicated=False,
        partial_write=True,
        degraded=False,
    )
    assert status.health == "partial"
    assert not status.replicated
    assert status.as_dict()["health"] == "partial"


def test_replication_outbox_payload_restores_quote_types():
    observed_at = datetime(2026, 11, 20, tzinfo=timezone.utc)
    original = QuoteSnapshot(
        user_id="firebase-user",
        symbol="BTCUSDT",
        exchange="COINDCX",
        last_price=65000.25,
        source="COINDCX",
        observed_at=observed_at,
        payload={"price": 65000.25},
    )

    restored = _record_from_payload(QuoteSnapshot, _model_payload(original))

    assert restored.user_id == "firebase-user"
    assert restored.observed_at == observed_at
    assert restored.last_price == 65000.25


def test_relational_models_are_registered():
    assert Base.metadata.tables.keys() >= {
        "app_users",
        "quote_snapshots",
        "orders",
        "positions",
        "risk_configs",
        "risk_states",
        "audit_events",
        "notifications",
        "replication_outbox",
    }

    # Instantiate every persistence model to verify mapped constructors.
    AppUser(external_subject="subject-1")
    QuoteSnapshot(symbol="NIFTY", exchange="NSE")
    OrderRecord(
        client_order_id="order-0001",
        symbol="NIFTY",
        exchange="NSE",
        side="BUY",
        operation="ENTRY",
        mode="PAPER",
        quantity=1,
    )
    Position(user_id="subject-1", symbol="NIFTY", exchange="NSE")
    RiskConfig(user_id="subject-1")
    RiskState(user_id="subject-1", trading_day="2026-11-20")
    AuditEvent(event_type="order.created", action="create")
    Notification(channel="push", kind="alert")
    ReplicationOutbox(
        aggregate_type="OrderRecord",
        aggregate_id="order-0001",
    )

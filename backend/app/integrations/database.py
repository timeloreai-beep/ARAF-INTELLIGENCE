from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from decimal import Decimal
import uuid

from sqlalchemy import DateTime, Numeric, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings
from ..models import (
    AppUser,
    AuditEvent,
    Notification,
    OrderRecord,
    Position,
    QuoteSnapshot,
    ReplicationOutbox,
    RiskConfig,
    RiskState,
)

DATABASE_ERRORS = (SQLAlchemyError, OSError, TimeoutError)


@dataclass
class PersistenceStatus:
    operation: str = "write"
    primary_applied: bool = False
    backup_applied: bool = False
    replicated: bool = False
    degraded: bool = False
    partial_write: bool = False
    outbox_queued: bool = False
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def health(self) -> str:
        if self.primary_applied and self.backup_applied:
            return "healthy"
        if self.primary_applied or self.backup_applied:
            return "partial" if self.primary_applied else "degraded"
        return "offline"

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "health": self.health,
            "primary_applied": self.primary_applied,
            "backup_applied": self.backup_applied,
            "replicated": self.replicated,
            "degraded": self.degraded,
            "partial_write": self.partial_write,
            "outbox_queued": self.outbox_queued,
            "errors": dict(self.errors),
        }


def normalize_async_database_url(raw_url: str) -> str:
    """Convert common PostgreSQL URLs to SQLAlchemy asyncpg URLs."""
    if not raw_url:
        return ""
    url = make_url(raw_url.strip())
    drivername = url.drivername
    if drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+asyncpg")
    elif drivername == "postgresql+psycopg":
        url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    query.pop("sslmode", None)
    query.pop("channel_binding", None)
    return url.set(query=query).render_as_string(hide_password=False)


class DatabaseHub:
    def __init__(self) -> None:
        settings = get_settings()
        self.last_status = PersistenceStatus(operation="not_run")
        self.primary_url = normalize_async_database_url(settings.supabase_database_url)
        self.backup_url = normalize_async_database_url(settings.neon_database_url)
        self.engine: AsyncEngine | None = None
        self.backup_engine: AsyncEngine | None = None
        self.sessions: async_sessionmaker | None = None
        self.backup_sessions: async_sessionmaker | None = None

        if self.primary_url:
            self.engine = create_async_engine(
                self.primary_url,
                pool_pre_ping=True,
                connect_args={"ssl": "require"},
            )
            self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        if self.backup_url:
            self.backup_engine = create_async_engine(
                self.backup_url,
                pool_pre_ping=True,
                connect_args={"ssl": "require"},
            )
            self.backup_sessions = async_sessionmaker(
                self.backup_engine, expire_on_commit=False
            )

    @property
    def enabled(self) -> bool:
        return self.sessions is not None or self.backup_sessions is not None

    async def execute(
        self, statement: str, parameters: dict[str, Any] | None = None
    ) -> Any:
        """Run read-only raw SQL with primary/backup failover."""
        normalized = statement.lstrip().upper()
        if (
            not normalized.startswith("SELECT ")
            or ";" in normalized
            or " FOR UPDATE" in normalized
        ):
            raise ValueError("Raw SQL writes are disabled; use a typed persistence method")
        status = PersistenceStatus(operation="execute")
        self.last_status = status
        configured = False
        for name, sessions in (
            ("primary", self.sessions),
            ("backup", self.backup_sessions),
        ):
            if sessions is None:
                status.errors[name] = "not_configured"
                continue
            configured = True
            try:
                async with sessions() as session:
                    result = await session.execute(text(statement), parameters or {})
                    rows = result.mappings().all()
                    if name == "primary":
                        status.primary_applied = True
                    else:
                        status.backup_applied = True
                        status.degraded = True
                    status.replicated = status.primary_applied and status.backup_applied
                    status.partial_write = status.primary_applied != status.backup_applied
                    return rows
            except DATABASE_ERRORS as exc:
                status.errors[name] = type(exc).__name__
        if not configured:
            raise RuntimeError("No relational database is configured")
        raise RuntimeError(
            f"Database read failed: {next(iter(status.errors.values()))}"
        )

    async def _merge_one(
        self, sessions: async_sessionmaker, record: Any
    ) -> Any:
        async with sessions() as session:
            stored = await session.merge(record)
            await session.commit()
            return stored

    async def write_model(
        self,
        record: Any,
        *,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        operation: str = "upsert",
    ) -> PersistenceStatus:
        status = PersistenceStatus(operation=operation)
        primary_error: Exception | None = None

        if self.sessions:
            try:
                record = await self._merge_one(self.sessions, record)
                status.primary_applied = True
            except DATABASE_ERRORS as exc:
                primary_error = exc
                status.errors["primary"] = type(exc).__name__
        else:
            status.errors["primary"] = "not_configured"

        if self.backup_sessions:
            try:
                record = await self._merge_one(self.backup_sessions, record)
                status.backup_applied = True
            except DATABASE_ERRORS as exc:
                status.errors["backup"] = type(exc).__name__

        status.replicated = status.primary_applied and status.backup_applied
        status.partial_write = (
            status.primary_applied and not status.backup_applied
        ) or (not status.primary_applied and status.backup_applied)
        status.degraded = status.backup_applied and not status.primary_applied
        self.last_status = status

        if status.partial_write and aggregate_type:
            outbox = ReplicationOutbox(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id or getattr(record, "id", "unknown"),
                operation=operation,
                target="backup" if status.primary_applied else "primary",
                status="pending",
                payload=_model_payload(record),
            )
            if status.primary_applied and self.sessions:
                try:
                    await self._merge_one(self.sessions, outbox)
                    status.outbox_queued = True
                except DATABASE_ERRORS as exc:
                    status.errors["outbox_primary"] = type(exc).__name__
            elif status.backup_applied and self.backup_sessions:
                try:
                    await self._merge_one(self.backup_sessions, outbox)
                    status.outbox_queued = True
                except DATABASE_ERRORS as exc:
                    status.errors["outbox_backup"] = type(exc).__name__

        if primary_error is not None and status.backup_applied:
            status.errors.setdefault("fallback", "primary_failed_backup_applied")
        return status

    async def write_many(
        self, records: Iterable[Any]
    ) -> PersistenceStatus:
        status = PersistenceStatus(operation="batch_upsert")
        items = list(records)
        if not items:
            return status
        for sessions_name in ("primary", "backup"):
            sessions = self.sessions if sessions_name == "primary" else self.backup_sessions
            if sessions is None:
                status.errors[sessions_name] = "not_configured"
                continue
            try:
                async with sessions() as session:
                    for record in items:
                        await session.merge(record)
                    await session.commit()
                if sessions_name == "primary":
                    status.primary_applied = True
                else:
                    status.backup_applied = True
            except DATABASE_ERRORS as exc:
                status.errors[sessions_name] = type(exc).__name__
        status.replicated = status.primary_applied and status.backup_applied
        status.partial_write = status.primary_applied != status.backup_applied
        status.degraded = status.backup_applied and not status.primary_applied
        self.last_status = status
        return status

    async def enqueue_outbox(
        self,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any] | None,
        *,
        operation: str = "upsert",
        target: str = "backup",
    ) -> PersistenceStatus:
        return await self.write_model(
            ReplicationOutbox(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                operation=operation,
                target=target,
                status="pending",
                payload=payload,
            ),
            aggregate_type="ReplicationOutbox",
            aggregate_id=aggregate_id,
        )

    async def upsert_app_user(self, **values: Any) -> PersistenceStatus:
        subject = str(values.get("external_subject", values.get("id", "unknown")))
        values.setdefault(
            "id",
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"app-user:{subject}")),
        )
        return await self.write_model(
            AppUser(**values),
            aggregate_type="AppUser",
            aggregate_id=subject,
        )

    async def upsert_quote_snapshot(self, **values: Any) -> PersistenceStatus:
        values.setdefault("id", str(uuid.uuid4()))
        return await self.write_model(
            QuoteSnapshot(**values),
            aggregate_type="QuoteSnapshot",
            aggregate_id=str(values.get("id", "unknown")),
        )

    async def persist_quote(
        self, subject: str, record: dict[str, Any]
    ) -> PersistenceStatus:
        observed_at = datetime.fromisoformat(record["timestamp"])
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        return await self.upsert_quote_snapshot(
            user_id=subject,
            symbol=str(record["symbol"]),
            exchange=str(record["exchange"]),
            last_price=float(record["price"]),
            source=str(record["source"]),
            observed_at=observed_at,
            payload=record,
        )

    async def upsert_order(self, **values: Any) -> PersistenceStatus:
        natural_id = (
            f"{values.get('user_id', '')}:"
            f"{values.get('client_order_id', values.get('id', 'unknown'))}"
        )
        values.setdefault(
            "id",
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"order:{natural_id}")),
        )
        return await self.write_model(
            OrderRecord(**values),
            aggregate_type="OrderRecord",
            aggregate_id=natural_id,
        )

    async def upsert_position(self, **values: Any) -> PersistenceStatus:
        natural_id = (
            f"{values.get('user_id', '')}:{values.get('symbol', '')}:"
            f"{values.get('exchange', '')}"
        )
        values.setdefault(
            "id",
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"position:{natural_id}")),
        )
        return await self.write_model(
            Position(**values),
            aggregate_type="Position",
            aggregate_id=natural_id,
        )

    async def upsert_risk_config(self, **values: Any) -> PersistenceStatus:
        return await self.write_model(
            RiskConfig(**values),
            aggregate_type="RiskConfig",
            aggregate_id=str(values.get("user_id", "unknown")),
        )

    async def upsert_risk_state(self, **values: Any) -> PersistenceStatus:
        natural_id = f"{values.get('user_id', '')}:{values.get('trading_day', '')}"
        values.setdefault(
            "id",
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"risk-state:{natural_id}")),
        )
        return await self.write_model(
            RiskState(**values),
            aggregate_type="RiskState",
            aggregate_id=natural_id,
        )

    async def add_audit_event(self, **values: Any) -> PersistenceStatus:
        values.setdefault("id", str(uuid.uuid4()))
        return await self.write_model(
            AuditEvent(**values),
            aggregate_type="AuditEvent",
            aggregate_id=str(values.get("id", "unknown")),
        )

    async def upsert_notification(self, **values: Any) -> PersistenceStatus:
        values.setdefault("id", str(uuid.uuid4()))
        return await self.write_model(
            Notification(**values),
            aggregate_type="Notification",
            aggregate_id=str(values.get("id", "unknown")),
        )

    async def get_app_user(self, external_subject: str) -> AppUser | None:
        return await self._get_one(AppUser, AppUser.external_subject == external_subject)

    async def get_order(self, user_id: str, client_order_id: str) -> OrderRecord | None:
        return await self._get_one(
            OrderRecord,
            (OrderRecord.user_id == user_id)
            & (OrderRecord.client_order_id == client_order_id),
        )

    async def get_risk_config(self, user_id: str) -> RiskConfig | None:
        return await self._get_one(RiskConfig, RiskConfig.user_id == user_id)

    async def get_risk_state(
        self, user_id: str, trading_day: str
    ) -> RiskState | None:
        return await self._get_one(
            RiskState,
            (RiskState.user_id == user_id) & (RiskState.trading_day == trading_day),
        )

    async def list_positions(self, user_id: str, exchange: str) -> list[Position]:
        statement = select(Position).where(
            (Position.user_id == user_id) & (Position.exchange == exchange)
        )
        errors: dict[str, str] = {}
        for sessions in (self.sessions, self.backup_sessions):
            if sessions is None:
                continue
            try:
                async with sessions() as session:
                    result = await session.execute(statement)
                    positions = list(result.scalars().all())
                    if errors and not positions:
                        raise RuntimeError("Position read used a failover database")
                    return positions
            except DATABASE_ERRORS as exc:
                errors["database"] = type(exc).__name__
        if errors:
            raise RuntimeError(f"Position read failed: {next(iter(errors.values()))}")
        if not self.enabled:
            raise RuntimeError("No relational database is configured")
        return []

    async def list_outbox(
        self, limit: int = 100, status: str = "pending"
    ) -> Sequence[ReplicationOutbox]:
        if not 1 <= limit <= 1000:
            raise ValueError("outbox limit must be between 1 and 1000")
        stmt = (
            select(ReplicationOutbox)
            .where(ReplicationOutbox.status == status)
            .order_by(ReplicationOutbox.created_at)
            .limit(limit)
        )
        errors: dict[str, str] = {}
        for sessions in (self.sessions, self.backup_sessions):
            if sessions is None:
                continue
            try:
                async with sessions() as session:
                    result = await session.execute(stmt)
                    rows = list(result.scalars().all())
                    if errors:
                        raise RuntimeError("Outbox read used a failover database")
                    return rows
            except (SQLAlchemyError, OSError, TimeoutError) as exc:
                errors["database"] = type(exc).__name__
                continue
        if errors:
            raise RuntimeError(f"Outbox read failed: {next(iter(errors.values()))}")
        if not self.enabled:
            raise RuntimeError("No relational database is configured")
        return []

    async def flush_outbox(self, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 1000:
            raise ValueError("outbox limit must be between 1 and 1000")
        report: dict[str, Any] = {"processed": 0, "errors": {}}
        model_types = {
            "AppUser": AppUser,
            "AuditEvent": AuditEvent,
            "Notification": Notification,
            "OrderRecord": OrderRecord,
            "Position": Position,
            "QuoteSnapshot": QuoteSnapshot,
            "RiskConfig": RiskConfig,
            "RiskState": RiskState,
        }
        sources = (
            ("primary", self.sessions),
            ("backup", self.backup_sessions),
        )
        for source_name, source_sessions in sources:
            if source_sessions is None:
                continue
            try:
                async with source_sessions() as session:
                    result = await session.execute(
                        select(ReplicationOutbox)
                        .where(ReplicationOutbox.status == "pending")
                        .order_by(ReplicationOutbox.created_at)
                        .limit(limit)
                    )
                    pending = list(result.scalars().all())
            except (SQLAlchemyError, OSError, TimeoutError) as exc:
                report["errors"][source_name] = type(exc).__name__
                continue

            for entry in pending:
                target_sessions = (
                    self.backup_sessions if entry.target == "backup" else self.sessions
                )
                target_name = entry.target
                model_type = model_types.get(entry.aggregate_type)
                if target_sessions is None or model_type is None or entry.operation != "upsert":
                    report["errors"][entry.id] = "unsupported_target_or_operation"
                    continue
                try:
                    record = _record_from_payload(model_type, entry.payload or {})
                    async with target_sessions() as target_session:
                        await target_session.merge(record)
                        await target_session.commit()
                    async with source_sessions() as source_session:
                        outbox_row = await source_session.get(ReplicationOutbox, entry.id)
                        if outbox_row and outbox_row.status == "pending":
                            outbox_row.status = "delivered"
                            outbox_row.delivered_at = datetime.now(timezone.utc)
                            outbox_row.attempts += 1
                            await source_session.commit()
                    report["processed"] += 1
                except (SQLAlchemyError, OSError, TimeoutError, ValueError, TypeError) as exc:
                    report["errors"][entry.id] = type(exc).__name__
                    try:
                        async with source_sessions() as source_session:
                            outbox_row = await source_session.get(ReplicationOutbox, entry.id)
                            if outbox_row:
                                outbox_row.attempts += 1
                                outbox_row.last_error = type(exc).__name__
                                await source_session.commit()
                    except (SQLAlchemyError, OSError, TimeoutError):
                        report["errors"][f"{entry.id}:outbox"] = "update_failed"
        return report

    async def _get_one(self, model: type[Any], where: Any) -> Any | None:
        stmt = select(model).where(where).limit(1)
        errors: dict[str, str] = {}
        for sessions in (self.sessions, self.backup_sessions):
            if sessions is None:
                continue
            try:
                async with sessions() as session:
                    result = await session.execute(stmt)
                    record = result.scalars().first()
                    if errors and record is None:
                        raise RuntimeError("Database read used a failover database")
                    return record
            except (SQLAlchemyError, OSError, TimeoutError) as exc:
                errors["database"] = type(exc).__name__
                continue
        if errors:
            raise RuntimeError(f"Database read failed: {next(iter(errors.values()))}")
        if not self.enabled:
            raise RuntimeError("No relational database is configured")
        return None

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()
        if self.backup_engine:
            await self.backup_engine.dispose()


def _model_payload(record: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    table = getattr(record, "__table__", None)
    if table is None:
        return payload
    for column in table.columns:
        value = getattr(record, column.name, None)
        if value is None:
            if column.nullable or column.default is not None or column.server_default is not None:
                continue
            raise ValueError(f"Required persistence field {column.name} is missing")
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, uuid.UUID):
            value = str(value)
        payload[column.name] = value
    return payload


def _record_from_payload(model_type: type[Any], payload: dict[str, Any]) -> Any:
    values = dict(payload)
    for column in model_type.__table__.columns:
        value = values.get(column.name)
        if value is None:
            continue
        if isinstance(column.type, DateTime) and isinstance(value, str):
            values[column.name] = datetime.fromisoformat(value)
        elif isinstance(column.type, Numeric) and isinstance(value, str):
            values[column.name] = Decimal(value)
    return model_type(**values)

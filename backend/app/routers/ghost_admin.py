from __future__ import annotations

import calendar
from collections import deque
from datetime import datetime, time as datetime_time, timedelta, timezone
import hmac
import logging
from pathlib import Path
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPIError
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..config import APEX_MAX_DAILY_SETUPS, get_settings
from ..dependencies import get_risk_guardian, require_ghost_admin
from ..schemas import (
    GhostAdminLogin,
    GhostAutoTradeControl,
    GhostSubscriptionUpdate,
    GhostSubscriptionTerm,
    GhostActivationRequest,
    GhostSystemConfigurationUpdate,
    GhostUserAccessUpdate,
    GhostUserVipUpdate,
)
from ..security import create_ghost_admin_token
from ..services.risk import RiskGuardian
from ..services.risk_persistence import load_risk_state, persist_risk_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/ghost", tags=["ghost-admin"])
activation_router = APIRouter(prefix="/api/admin", tags=["admin-activation"])
_attempts: dict[str, deque[float]] = {}
_attempts_lock = threading.Lock()
_MAX_LOGIN_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 900
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
_DEFAULT_MONTHLY_FEE_INR = 299
_APPROVED_KYC_STATUSES = {"APPROVED", "VERIFIED_COMPLIANT"}


def _login_is_limited(address: str) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        attempts = _attempts.setdefault(address, deque())
        while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= _MAX_LOGIN_FAILURES


def _record_login_failure(address: str) -> None:
    now = time.monotonic()
    with _attempts_lock:
        attempts = _attempts.setdefault(address, deque())
        while attempts and now - attempts[0] > _LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        attempts.append(now)


@router.get("/page", include_in_schema=False)
async def ghost_admin_page() -> FileResponse:
    return FileResponse(_PROJECT_ROOT / "LEGACY_JS_BACKUP" / "admin-ghost-control.html")


@router.post("/login")
async def ghost_admin_login(payload: GhostAdminLogin, request: Request) -> dict:
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Ghost admin password is not configured")
    address = request.client.host if request.client else "unknown"
    if _login_is_limited(address):
        raise HTTPException(status_code=429, detail="Too many failed admin login attempts")
    if not hmac.compare_digest(payload.password, settings.admin_password):
        _record_login_failure(address)
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    with _attempts_lock:
        _attempts.pop(address, None)
    try:
        token = create_ghost_admin_token("ghost-admin")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Admin token signing secret is not configured") from exc
    return {"access_token": token, "token_type": "bearer", "expires_in": 900}


@router.get("/control")
async def get_control(
    request: Request,
    _admin: str = Depends(require_ghost_admin),
) -> dict:
    database = request.app.state.database
    settings = get_settings()
    control = None
    if database.enabled:
        try:
            control = await database.get_admin_control()
        except (SQLAlchemyError, RuntimeError) as exc:
            logger.exception("Could not load persisted admin controls")
            raise HTTPException(status_code=503, detail="Admin control persistence is unavailable") from exc
    return {
        "success": True,
        "auto_trade_enabled": bool(control and control.auto_trade_enabled),
        "global_kill_switch": bool(control and control.global_kill_switch),
        "kill_switch_reason": control.kill_switch_reason if control else None,
        "updated_at": control.updated_at if control else None,
        "limits": {
            "max_daily_trades": min(settings.max_daily_trades, APEX_MAX_DAILY_SETUPS),
            "max_per_trade_risk_pct": settings.max_per_trade_risk_pct,
            "max_daily_loss_amount": settings.max_daily_loss_amount,
        },
        "live_trading_enabled": False,
    }


def _require_primary_persistence(persistence, detail: str) -> None:
    if not persistence.primary_applied:
        logger.error(
            "%s; Supabase persistence status: %s",
            detail,
            persistence.as_dict(),
        )
        raise HTTPException(status_code=503, detail=detail)


async def _require_ghost_user(database, subject: str):
    if not database.enabled:
        raise HTTPException(status_code=503, detail="User management storage is unavailable")
    try:
        user = await database.get_app_user(subject)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load a user for Ghost Admin")
        raise HTTPException(status_code=503, detail="User management storage is unavailable") from exc
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _user_can_trade(user) -> bool:
    expires_at = user.subscription_expires_at
    if expires_at is not None:
        expires_at = (
            expires_at.replace(tzinfo=timezone.utc)
            if expires_at.tzinfo is None
            else expires_at.astimezone(timezone.utc)
        )
    return bool(
        user.is_active
        and user.kyc_status in _APPROVED_KYC_STATUSES
        and (user.is_vip_free or (expires_at and expires_at > datetime.now(timezone.utc)))
    )


def _subscription_expiry_from_date(expiry_date) -> datetime:
    expiry_ist = datetime.combine(
        expiry_date + timedelta(days=1),
        datetime_time.min,
        tzinfo=_INDIA_TIMEZONE,
    ) - timedelta(microseconds=1)
    return expiry_ist.astimezone(timezone.utc)


def _add_calendar_months(value, months: int):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _sync_guardian_access(guardian: RiskGuardian, user) -> None:
    guardian.sync_user_access(user.external_subject, _user_can_trade(user))


def _format_user_row(user, kyc) -> dict:
    expires_at = user.subscription_expires_at or (
        kyc.trial_expires_at if kyc else None
    )
    normalized_expiry = expires_at
    if normalized_expiry is not None:
        normalized_expiry = (
            normalized_expiry.replace(tzinfo=timezone.utc)
            if normalized_expiry.tzinfo is None
            else normalized_expiry.astimezone(timezone.utc)
        )
    if not user.is_active:
        subscription_status = "REVOKED"
    elif user.is_vip_free:
        subscription_status = "VIP_FREE"
    elif normalized_expiry and normalized_expiry > datetime.now(timezone.utc):
        subscription_status = "VIP_ACTIVE"
    elif expires_at:
        subscription_status = "EXPIRED"
    else:
        subscription_status = "FREE"
    return {
        "user_id": user.external_subject,
        "user_name": (kyc.full_name if kyc else None) or user.display_name or "—",
        "email": user.email or "—",
        "subscription_status": subscription_status,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "kyc_status": user.kyc_status,
        "is_vip_free": bool(user.is_vip_free),
        "is_subscribed": bool(
            user.is_vip_free
            or (
                normalized_expiry
                and normalized_expiry > datetime.now(timezone.utc)
            )
        ),
        "is_active": bool(user.is_active),
    }


@router.get("/users")
async def list_users(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    _admin: str = Depends(require_ghost_admin),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="User management storage is unavailable")
    try:
        users = await database.list_app_users(limit)
        kyc_records = await database.list_kyc_records(
            [user.external_subject for user in users]
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load Ghost Admin user list")
        raise HTTPException(status_code=503, detail="User management storage is unavailable") from exc
    kyc_by_user = {record.user_id: record for record in kyc_records}
    return {
        "success": True,
        "users": [_format_user_row(user, kyc_by_user.get(user.external_subject)) for user in users],
    }


@router.post("/users/{subject}/kyc/approve")
async def approve_user_kyc(
    subject: str,
    request: Request,
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    database = request.app.state.database
    user = await _require_ghost_user(database, subject)
    user.kyc_status = "APPROVED"
    user.updated_at = datetime.now(timezone.utc)
    try:
        kyc = await database.get_kyc_record(subject)
        records = [user]
        if kyc:
            kyc.status = "APPROVED"
            records.append(kyc)
        persistence = await database.write_many(records)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not approve user KYC")
        raise HTTPException(status_code=503, detail="KYC update could not be persisted") from exc
    _require_primary_persistence(persistence, "KYC update could not be persisted to Supabase")
    _sync_guardian_access(guardian, user)
    return {
        "success": True,
        "user_id": subject,
        "kyc_status": user.kyc_status,
        "risk_access_allowed": guardian.user_access_allowed(subject),
        "persistence": persistence.as_dict(),
    }


@router.post("/users/{subject}/vip")
async def update_user_vip(
    subject: str,
    payload: GhostUserVipUpdate,
    request: Request,
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    database = request.app.state.database
    user = await _require_ghost_user(database, subject)
    user.is_vip_free = payload.is_vip_free
    expiry = user.subscription_expires_at
    if expiry is not None:
        expiry = (
            expiry.replace(tzinfo=timezone.utc)
            if expiry.tzinfo is None
            else expiry.astimezone(timezone.utc)
        )
    user.is_subscribed = bool(
        payload.is_vip_free or (expiry and expiry > datetime.now(timezone.utc))
    )
    user.updated_at = datetime.now(timezone.utc)
    try:
        persistence = await database.upsert_app_user(
            external_subject=user.external_subject,
            provider=user.provider,
            email=user.email,
            display_name=user.display_name,
            trader_uid=user.trader_uid,
            role=user.role,
            kyc_status=user.kyc_status,
            is_active=user.is_active,
            is_vip_free=user.is_vip_free,
            is_subscribed=user.is_subscribed,
            subscription_expires_at=user.subscription_expires_at,
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not update VIP status")
        raise HTTPException(status_code=503, detail="Subscription update could not be persisted") from exc
    _require_primary_persistence(persistence, "Subscription update could not be persisted to Supabase")
    _sync_guardian_access(guardian, user)
    return {
        "success": True,
        "user_id": subject,
        "is_vip_free": user.is_vip_free,
        "risk_access_allowed": guardian.user_access_allowed(subject),
        "persistence": persistence.as_dict(),
    }


@router.post("/users/{subject}/subscription")
async def extend_user_subscription(
    subject: str,
    payload: GhostSubscriptionUpdate,
    request: Request,
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    today = datetime.now(_INDIA_TIMEZONE).date()
    if payload.expires_on < today:
        raise HTTPException(status_code=422, detail="Expiry date cannot be in the past")
    expiry_ist = datetime.combine(
        payload.expires_on + timedelta(days=1),
        datetime_time.min,
        tzinfo=_INDIA_TIMEZONE,
    ) - timedelta(microseconds=1)
    expires_at = expiry_ist.astimezone(timezone.utc)
    database = request.app.state.database
    user = await _require_ghost_user(database, subject)
    user.subscription_expires_at = expires_at
    user.is_vip_free = False
    user.is_subscribed = True
    user.updated_at = datetime.now(timezone.utc)
    try:
        persistence = await database.upsert_app_user(
            external_subject=user.external_subject,
            provider=user.provider,
            email=user.email,
            display_name=user.display_name,
            trader_uid=user.trader_uid,
            role=user.role,
            kyc_status=user.kyc_status,
            is_active=user.is_active,
            is_vip_free=False,
            is_subscribed=True,
            subscription_expires_at=expires_at,
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not extend user subscription")
        raise HTTPException(status_code=503, detail="Subscription update could not be persisted") from exc
    _require_primary_persistence(persistence, "Subscription update could not be persisted to Supabase")
    _sync_guardian_access(guardian, user)
    return {
        "success": True,
        "user_id": subject,
        "subscription_expires_at": expires_at.isoformat(),
        "risk_access_allowed": guardian.user_access_allowed(subject),
        "persistence": persistence.as_dict(),
    }


@router.post("/users/{subject}/subscription/extend")
async def extend_user_subscription_by_term(
    subject: str,
    payload: GhostSubscriptionTerm,
    request: Request,
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    database = request.app.state.database
    user = await _require_ghost_user(database, subject)
    now = datetime.now(timezone.utc)
    current_expiry = user.subscription_expires_at
    if current_expiry is not None:
        current_expiry = (
            current_expiry.replace(tzinfo=timezone.utc)
            if current_expiry.tzinfo is None
            else current_expiry.astimezone(timezone.utc)
        )
    base = (
        current_expiry.astimezone(_INDIA_TIMEZONE).date()
        if current_expiry and current_expiry > now
        else now.astimezone(_INDIA_TIMEZONE).date()
    )
    expiry_date = _add_calendar_months(base, payload.months)
    expires_at = _subscription_expiry_from_date(expiry_date)
    user.subscription_expires_at = expires_at
    user.is_vip_free = False
    user.is_subscribed = True
    user.updated_at = now
    try:
        persistence = await database.upsert_app_user(
            external_subject=user.external_subject,
            provider=user.provider,
            email=user.email,
            display_name=user.display_name,
            trader_uid=user.trader_uid,
            role=user.role,
            kyc_status=user.kyc_status,
            is_active=user.is_active,
            is_vip_free=False,
            is_subscribed=True,
            subscription_expires_at=expires_at,
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not extend user subscription by term")
        raise HTTPException(status_code=503, detail="Subscription update could not be persisted") from exc
    _require_primary_persistence(persistence, "Subscription update could not be persisted to Supabase")
    _sync_guardian_access(guardian, user)
    return {
        "success": True,
        "user_id": subject,
        "months_added": payload.months,
        "subscription_expires_at": expires_at.isoformat(),
        "is_subscribed": True,
        "risk_access_allowed": guardian.user_access_allowed(subject),
        "persistence": persistence.as_dict(),
    }


@activation_router.post("/activate-user")
async def activate_user_by_custom_months(
    payload: GhostActivationRequest,
    request: Request,
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    """Named API alias for Ghost Telegram and external admin activation flows."""
    return await extend_user_subscription_by_term(
        payload.subject,
        GhostSubscriptionTerm(months=payload.months),
        request,
        _admin,
        guardian,
    )


async def activate_subject_by_months(database, guardian, subject: str, months: int) -> dict:
    if not 1 <= months <= 12:
        raise HTTPException(status_code=422, detail="Months must be between 1 and 12")
    user = await _require_ghost_user(database, subject)
    now = datetime.now(timezone.utc)
    current_expiry = user.subscription_expires_at
    if current_expiry is not None:
        current_expiry = (
            current_expiry.replace(tzinfo=timezone.utc)
            if current_expiry.tzinfo is None
            else current_expiry.astimezone(timezone.utc)
        )
    base = (
        current_expiry.astimezone(_INDIA_TIMEZONE).date()
        if current_expiry and current_expiry > now
        else now.astimezone(_INDIA_TIMEZONE).date()
    )
    expiry_date = _add_calendar_months(base, months)
    expires_at = _subscription_expiry_from_date(expiry_date)
    user.subscription_expires_at = expires_at
    user.is_vip_free = False
    user.is_subscribed = True
    user.updated_at = now
    try:
        persistence = await database.upsert_app_user(
            external_subject=user.external_subject,
            provider=user.provider,
            email=user.email,
            display_name=user.display_name,
            trader_uid=user.trader_uid,
            role=user.role,
            kyc_status=user.kyc_status,
            is_active=user.is_active,
            is_vip_free=False,
            is_subscribed=True,
            subscription_expires_at=expires_at,
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Subscription update could not be persisted",
        ) from exc
    _require_primary_persistence(
        persistence,
        "Subscription update could not be persisted to Supabase",
    )
    _sync_guardian_access(guardian, user)
    return {
        "success": True,
        "user_id": subject,
        "months_added": months,
        "subscription_expires_at": expires_at.isoformat(),
        "is_subscribed": True,
    }


@router.post("/users/{subject}/access")
async def update_user_access(
    subject: str,
    payload: GhostUserAccessUpdate,
    request: Request,
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    database = request.app.state.database
    user = await _require_ghost_user(database, subject)
    user.is_active = payload.is_active
    user.updated_at = datetime.now(timezone.utc)
    try:
        persistence = await database.upsert_app_user(
            external_subject=user.external_subject,
            provider=user.provider,
            email=user.email,
            display_name=user.display_name,
            trader_uid=user.trader_uid,
            role=user.role,
            kyc_status=user.kyc_status,
            is_active=user.is_active,
            is_vip_free=user.is_vip_free,
            is_subscribed=user.is_subscribed,
            subscription_expires_at=user.subscription_expires_at,
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not update user access")
        raise HTTPException(status_code=503, detail="Access update could not be persisted") from exc
    _require_primary_persistence(persistence, "Access update could not be persisted to Supabase")
    _sync_guardian_access(guardian, user)
    return {
        "success": True,
        "user_id": subject,
        "is_active": user.is_active,
        "risk_access_allowed": guardian.user_access_allowed(subject),
        "persistence": persistence.as_dict(),
    }


@router.get("/configuration")
async def get_system_configuration(
    request: Request,
    _admin: str = Depends(require_ghost_admin),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="System configuration storage is unavailable")
    try:
        configuration = await database.get_system_configuration()
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load Ghost Admin system configuration")
        raise HTTPException(status_code=503, detail="System configuration storage is unavailable") from exc
    fee = (
        float(configuration.global_monthly_fee_inr)
        if configuration
        else _DEFAULT_MONTHLY_FEE_INR
    )
    return {"success": True, "global_monthly_fee_inr": fee}


@router.post("/configuration")
async def update_system_configuration(
    payload: GhostSystemConfigurationUpdate,
    request: Request,
    admin: str = Depends(require_ghost_admin),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="System configuration storage is unavailable")
    try:
        persistence = await database.upsert_system_configuration(
            global_monthly_fee_inr=payload.global_monthly_fee_inr,
            updated_by=admin,
        )
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not persist Ghost Admin system configuration")
        raise HTTPException(status_code=503, detail="System configuration update failed") from exc
    _require_primary_persistence(persistence, "System configuration could not be persisted to Supabase")
    return {
        "success": True,
        "global_monthly_fee_inr": payload.global_monthly_fee_inr,
        "persistence": persistence.as_dict(),
    }


@router.get("/pricing")
async def get_public_subscription_pricing(request: Request) -> dict:
    """Expose the current product fee; administration of it remains password-protected."""
    database = request.app.state.database
    if not database.enabled:
        return {"success": True, "global_monthly_fee_inr": _DEFAULT_MONTHLY_FEE_INR}
    try:
        configuration = await database.get_system_configuration()
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not load public subscription pricing")
        raise HTTPException(status_code=503, detail="Subscription pricing is unavailable") from exc
    fee = (
        float(configuration.global_monthly_fee_inr)
        if configuration
        else _DEFAULT_MONTHLY_FEE_INR
    )
    return {"success": True, "global_monthly_fee_inr": fee}


@router.post("/control")
async def update_control(
    payload: GhostAutoTradeControl,
    request: Request,
    admin: str = Depends(require_ghost_admin),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Durable admin control storage is required")
    try:
        current = await database.get_admin_control()
        auto_trade_enabled = (
            payload.enabled
            if payload.enabled is not None
            else bool(current and current.auto_trade_enabled)
        )
        global_kill_switch = (
            payload.global_kill_switch
            if payload.global_kill_switch is not None
            else bool(current and current.global_kill_switch)
        )
        if auto_trade_enabled and global_kill_switch:
            raise HTTPException(
                status_code=409,
                detail="Clear the global kill switch before starting auto-trade",
            )
        control_values = {
            "id": "global",
            "auto_trade_enabled": auto_trade_enabled,
            "global_kill_switch": global_kill_switch,
            "kill_switch_reason": payload.reason if global_kill_switch else None,
            "updated_by": admin,
        }
        status_report = await database.upsert_admin_control(**control_values)
    except (SQLAlchemyError, RuntimeError) as exc:
        logger.exception("Could not persist admin controls")
        raise HTTPException(status_code=503, detail="Admin control persistence failed") from exc
    if not (status_report.primary_applied or status_report.backup_applied):
        raise HTTPException(status_code=503, detail="Admin control could not be stored in either relational database")
    request.app.state.auto_trade_enabled = auto_trade_enabled

    audit_status = "stored"
    try:
        audit = await database.add_audit_event(
            actor_id=admin,
            event_type="admin.auto_trade_control",
            resource_type="admin_control",
            resource_id="global",
            action="update",
            outcome="success",
            payload={
                "reason": payload.reason,
                "auto_trade_enabled": auto_trade_enabled,
                "global_kill_switch": global_kill_switch,
            },
        )
        audit_status = audit.health
    except (SQLAlchemyError, RuntimeError):
        logger.exception("Could not audit admin control update")
        audit_status = "error"
    current = await database.get_admin_control()
    return {
        "success": True,
        "auto_trade_enabled": bool(current and current.auto_trade_enabled),
        "global_kill_switch": bool(current and current.global_kill_switch),
        "persistence": status_report.as_dict(),
        "audit": audit_status,
        "live_trading_enabled": False,
    }


@router.post("/risk/reset")
async def reset_user_risk_lock(
    request: Request,
    target: str = Query(min_length=1, max_length=255),
    _admin: str = Depends(require_ghost_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    database = request.app.state.database
    redis = request.app.state.redis
    if not database.enabled and not redis.enabled:
        raise HTTPException(status_code=503, detail="A persistent risk store is required")
    try:
        await load_risk_state(request, target, guardian)
        state = guardian.reset(target, admin_override=True)
        persistence = await persist_risk_state(request, target, state)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.exception("Could not reset the user risk lock")
        raise HTTPException(status_code=503, detail="Risk lock persistence is unavailable") from exc
    postgres = persistence.get("postgres", {})
    postgres_saved = any(
        isinstance(status, dict)
        and (status.get("primary_applied") or status.get("backup_applied"))
        for status in postgres.values()
    )
    if persistence.get("redis") != "stored" and not postgres_saved:
        raise HTTPException(status_code=503, detail="Risk lock reset could not be stored")
    return {
        "success": True,
        "target": target,
        "risk": state.__dict__,
        "persistence": persistence,
    }


@router.get("/scanner")
async def scanner_feed(
    request: Request,
    limit: int = 20,
    _admin: str = Depends(require_ghost_admin),
) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    mongo = request.app.state.mongo
    if not mongo.enabled:
        return {"success": True, "status": "not_configured", "scans": []}
    try:
        records = await mongo.find("scanner_history", {}, limit=limit)
    except PyMongoError as exc:
        logger.exception("Could not load scanner history for Ghost Admin")
        raise HTTPException(status_code=503, detail="Scanner history is unavailable") from exc
    scans = [
        {
            "market": record.get("market"),
            "count": record.get("count", 0),
            "created_at": record.get("created_at"),
            "items": (record.get("items") or [])[:25],
        }
        for record in records
    ]
    return {"success": True, "status": "healthy", "scans": scans}


@router.get("/health")
async def integration_health(
    request: Request,
    _admin: str = Depends(require_ghost_admin),
) -> dict:
    settings = get_settings()
    database = request.app.state.database
    redis = request.app.state.redis
    mongo = request.app.state.mongo
    firebase = request.app.state.firebase

    db_status = await database.health()
    try:
        redis_status = "healthy" if redis.enabled and await redis.ping() else (
            "not_configured" if not redis.enabled else "error"
        )
    except RedisError:
        logger.exception("Upstash health probe failed")
        redis_status = "error"
    try:
        mongo_status = "healthy" if mongo.enabled and await mongo.ping() else (
            "not_configured" if not mongo.enabled else "error"
        )
    except PyMongoError:
        logger.exception("MongoDB health probe failed")
        mongo_status = "error"
    try:
        firebase_status = "healthy" if firebase.enabled and await firebase.ping() else (
            "not_configured" if not firebase.enabled else "error"
        )
    except (FirebaseError, GoogleAPIError, RuntimeError):
        logger.exception("Firebase health probe failed")
        firebase_status = "error"

    workflow = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    services = [
        {"name": "backend_ram", "status": "healthy"},
        {"name": "browser_local_storage", "status": "browser_only"},
        {"name": "upstash_redis", "status": redis_status},
        {"name": "supabase_postgres", "status": db_status["supabase"]},
        {"name": "mongodb_atlas", "status": mongo_status},
        {"name": "neon_postgres", "status": db_status["neon"]},
        {"name": "firebase_firestore", "status": firebase_status},
        {"name": "render_backend", "status": "healthy"},
        {
            "name": "github_actions",
            "status": "workflow_present" if workflow.is_file() else "not_configured",
            "detail": "Configuration only; latest workflow run is not probed",
        },
        {
            "name": "shoonya",
            "status": "configured"
            if all((settings.shoonya_api_key, settings.shoonya_user_id, settings.shoonya_password, settings.shoonya_totp_secret))
            else "not_configured",
        },
        {
            "name": "coindcx",
            "status": "configured"
            if all((settings.coindcx_api_key, settings.coindcx_api_secret))
            else "not_configured",
        },
    ]
    news = request.app.state.news_engine.snapshot()
    return {
        "success": True,
        "checked_at": datetime.now(timezone.utc),
        "services": services,
        "news_providers": news["providers"],
        "live_trading_enabled": False,
    }

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
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
from ..schemas import GhostAdminLogin, GhostAutoTradeControl
from ..security import create_ghost_admin_token
from ..services.risk import RiskGuardian
from ..services.risk_persistence import load_risk_state, persist_risk_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/ghost", tags=["ghost-admin"])
_attempts: dict[str, deque[float]] = {}
_attempts_lock = threading.Lock()
_MAX_LOGIN_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 900
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
    return FileResponse(_PROJECT_ROOT / "admin-ghost-control.html")


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

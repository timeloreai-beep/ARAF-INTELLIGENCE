import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from ..dependencies import current_subject, require_ghost_admin, get_risk_guardian
from ..schemas import RiskConfig, RiskPnlSync
from ..services.risk import INDIA_TIMEZONE, RiskGuardian
from ..services.risk_persistence import (
    is_persistence_degraded,
    load_risk_state,
    persist_risk_state,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])


def _has_durable_risk_store(request: Request) -> bool:
    return bool(
        getattr(request.app.state.database, "enabled", False)
        or getattr(request.app.state.redis, "enabled", False)
    )


def _risk_write_succeeded(report: dict) -> bool:
    if report.get("redis") == "stored":
        return True
    postgres = report.get("postgres", {})
    return any(
        isinstance(status, dict)
        and (status.get("primary_applied") or status.get("backup_applied"))
        for status in postgres.values()
    )


@router.post("/configure")
async def configure_risk(
    payload: RiskConfig,
    request: Request,
    subject: str = Depends(current_subject),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    if not _has_durable_risk_store(request):
        raise HTTPException(status_code=503, detail="A persistent risk store is required")
    try:
        await load_risk_state(request, subject, guardian)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    try:
        state = guardian.configure(
            subject,
            payload.capital_inr,
            payload.max_daily_loss_amount,
            payload.max_trades_per_day,
            payload.max_per_trade_risk_pct,
            payload.max_weekly_loss_amount,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        persistence = await persist_risk_state(request, subject, state)
    except SQLAlchemyError as exc:
        logger.exception("Could not persist risk configuration")
        raise HTTPException(status_code=503, detail="Risk configuration persistence failed") from exc
    if not _risk_write_succeeded(persistence):
        raise HTTPException(status_code=503, detail="Risk configuration could not be stored")
    return {
        "success": True,
        "risk": state.__dict__,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }


@router.get("/state")
async def risk_state(
    request: Request,
    subject: str = Depends(current_subject),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    if not _has_durable_risk_store(request):
        raise HTTPException(status_code=503, detail="A persistent risk store is required")
    try:
        state = await load_risk_state(request, subject, guardian)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    return {"success": True, "risk": state.__dict__}


@router.post("/pnl")
async def sync_daily_pnl(
    _client_reported_pnl: RiskPnlSync,
    request: Request,
    subject: str = Depends(current_subject),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    database = request.app.state.database
    if not getattr(database, "enabled", False):
        raise HTTPException(
            status_code=503,
            detail="Server-side paper P&L source is unavailable",
        )
    try:
        await load_risk_state(request, subject, guardian)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    trading_day = datetime.fromisoformat(guardian.current_trading_day()).date()
    start_at = datetime.combine(trading_day, datetime.min.time(), tzinfo=INDIA_TIMEZONE)
    end_at = start_at + timedelta(days=1)
    try:
        daily_pnl_inr = await database.get_daily_paper_pnl_inr(
            subject,
            start_at.astimezone(timezone.utc),
            end_at.astimezone(timezone.utc),
        )
        weekly_pnl_inr = None
        # Calculate weekly P&L (Monday to Sunday)
        week_start = start_at - timedelta(days=start_at.weekday())
        week_end = week_start + timedelta(days=7)
        weekly_pnl_inr = await database.get_daily_paper_pnl_inr(
            subject,
            week_start.astimezone(timezone.utc),
            week_end.astimezone(timezone.utc),
        )
        state = guardian.sync_pnl(subject, daily_pnl_inr, weekly_pnl_inr)
        persistence = await persist_risk_state(request, subject, state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Server-side paper P&L is unavailable") from exc
    except SQLAlchemyError as exc:
        logger.exception("Could not persist daily P&L risk state")
        raise HTTPException(status_code=503, detail="Daily P&L persistence failed") from exc
    if not _risk_write_succeeded(persistence):
        raise HTTPException(status_code=503, detail="Daily P&L could not be stored")
    return {
        "success": True,
        "risk": state.__dict__,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }


@router.post("/admin/reset")
async def reset_kill_switch(
    request: Request,
    subject: str = Depends(require_ghost_admin),
    target: str = "",
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    target_subject = target or subject
    if not _has_durable_risk_store(request):
        raise HTTPException(status_code=503, detail="A persistent risk store is required")
    try:
        await load_risk_state(request, target_subject, guardian)
        state = guardian.reset(target_subject, admin_override=True)
        persistence = await persist_risk_state(request, target_subject, state)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    if not _risk_write_succeeded(persistence):
        raise HTTPException(status_code=503, detail="Risk lock reset could not be stored")
    return {
        "success": True,
        "risk": state.__dict__,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }

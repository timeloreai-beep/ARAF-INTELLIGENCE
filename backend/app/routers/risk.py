import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from ..dependencies import current_subject, require_admin, get_risk_guardian
from ..schemas import RiskConfig
from ..services.risk import RiskGuardian
from ..services.risk_persistence import (
    is_persistence_degraded,
    load_risk_state,
    persist_risk_state,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])


@router.post("/configure")
async def configure_risk(
    payload: RiskConfig,
    request: Request,
    subject: str = Depends(current_subject),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    try:
        await load_risk_state(request, subject, guardian)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    try:
        state = guardian.configure(
            subject,
            payload.capital_inr,
            payload.max_daily_loss_pct,
            payload.max_trades_per_day,
            payload.max_per_trade_risk_pct,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        persistence = await persist_risk_state(request, subject, state)
    except SQLAlchemyError as exc:
        logger.exception("Could not persist risk configuration")
        raise HTTPException(status_code=503, detail="Risk configuration persistence failed") from exc
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
    try:
        state = await load_risk_state(request, subject, guardian)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    return {"success": True, "risk": state.__dict__}


@router.post("/admin/reset")
async def reset_kill_switch(
    request: Request,
    subject: str = Depends(require_admin),
    target: str = "",
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    target_subject = target or subject
    try:
        await load_risk_state(request, target_subject, guardian)
        state = guardian.reset(target_subject)
        persistence = await persist_risk_state(request, target_subject, state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
    return {
        "success": True,
        "risk": state.__dict__,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }

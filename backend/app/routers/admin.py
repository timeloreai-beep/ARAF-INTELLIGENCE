import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPIError

from ..dependencies import require_admin, get_risk_guardian
from ..services.risk import RiskGuardian
from ..services.risk_persistence import is_persistence_degraded, load_risk_state, persist_risk_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/global-kill-switch")
async def global_kill_switch(
    request: Request,
    target_subject: str,
    reason: str = "ADMIN_GLOBAL_KILL_SWITCH",
    admin: str = Depends(require_admin),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    try:
        await load_risk_state(request, target_subject, guardian)
        state = guardian.global_lock(target_subject, reason)
        persistence = await persist_risk_state(request, target_subject, state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc

    database = request.app.state.database
    if database.enabled:
        event = await database.add_audit_event(
            actor_id=admin,
            event_type="risk.global_kill_switch",
            resource_type="risk_state",
            resource_id=target_subject,
            action="lock",
            outcome="success",
            payload={"reason": reason},
        )
        persistence["audit_event"] = event.as_dict()

    notifications = "not_configured"
    firebase = request.app.state.firebase
    if firebase.enabled:
        try:
            message_ids = await firebase.send_to_subject(
                target_subject,
                "Trading halted",
                "An administrator activated the global risk kill-switch.",
                {"kind": "risk_kill_switch"},
            )
            notifications = "sent" if message_ids else "no_devices"
        except (FirebaseError, GoogleAPIError):
            logger.exception("Could not send global kill-switch notification")
            notifications = "error"
    if database.enabled:
        notification_status = await database.upsert_notification(
            recipient_id=target_subject,
            channel="push",
            kind="risk_kill_switch",
            subject="Trading halted",
            body="An administrator activated the global risk kill-switch.",
            status=notifications,
            payload={"reason": reason},
        )
        persistence["notification_record"] = notification_status.as_dict()
    persistence["firebase"] = notifications
    return {
        "success": True,
        "target": target_subject,
        "risk": state.__dict__,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }

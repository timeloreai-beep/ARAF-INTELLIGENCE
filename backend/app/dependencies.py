from fastapi import Header, HTTPException, Request, status
from firebase_admin.exceptions import FirebaseError
from starlette.concurrency import run_in_threadpool

from .security import decode_access_token
from .services.risk import RiskGuardian

risk_guardian = RiskGuardian()


def get_risk_guardian() -> RiskGuardian:
    return risk_guardian


async def _verified_claims(request: Request, authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload and payload.get("sub"):
        request.state.auth_claims = payload
        return payload

    firebase = getattr(request.app.state, "firebase", None)
    if firebase is None or not firebase.enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    try:
        payload = await run_in_threadpool(firebase.verify_id_token, token)
        request.state.auth_claims = payload
        return payload
    except (FirebaseError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from None


async def current_subject(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    payload = await _verified_claims(request, authorization)
    subject = payload.get("sub") or payload.get("uid")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return str(subject)


async def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> str:
    payload = await _verified_claims(request, authorization)
    if payload.get("role") != "admin" and payload.get("admin") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin authorization required")
    subject = payload.get("sub") or payload.get("uid")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return str(subject)

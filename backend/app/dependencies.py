import hashlib
import time

from fastapi import Header, HTTPException, Request, status
from firebase_admin.exceptions import FirebaseError
from redis.exceptions import RedisError
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
    else:
        firebase = getattr(request.app.state, "firebase", None)
        if firebase is None or not firebase.enabled:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        try:
            payload = await run_in_threadpool(firebase.verify_id_token, token)
            request.state.auth_claims = payload
        except (FirebaseError, ValueError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from None

    redis = getattr(request.app.state, "redis", None)
    if redis and redis.enabled:
        token_key = hashlib.sha256(token.encode()).hexdigest()
        try:
            if await redis.get(f"auth:revoked-token:{token_key}"):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token",
                )
        except RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Session validation temporarily unavailable",
            ) from exc
    return payload


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


async def require_ghost_admin(
    authorization: str | None = Header(default=None),
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    payload = decode_access_token(authorization.removeprefix("Bearer ").strip())
    if not payload or payload.get("scope") != "ghost_admin" or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ghost admin authorization required")
    return str(payload["sub"])

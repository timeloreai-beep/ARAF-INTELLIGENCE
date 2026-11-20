import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..dependencies import current_subject
from ..schemas import LoginRequest, TokenResponse
from ..security import create_access_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    # Credentials must be verified by a provider-backed user repository.
    raise HTTPException(
        status_code=501,
        detail="User provider not configured; add Supabase/Firebase identity adapter",
    )


@router.post("/pin/verify")
async def verify_pin() -> dict:
    raise HTTPException(status_code=501, detail="PIN repository not configured")


@router.post("/session")
async def sync_authenticated_session(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    claims = request.state.auth_claims
    database = request.app.state.database
    redis = request.app.state.redis
    role = "admin" if claims.get("role") == "admin" or claims.get("admin") is True else "user"
    persistence: dict[str, object] = {}

    if database.enabled:
        try:
            status = await database.upsert_app_user(
                external_subject=subject,
                provider="firebase",
                email=claims.get("email"),
                display_name=claims.get("name"),
                role=role,
                is_active=True,
            )
            persistence["postgres"] = status.as_dict()
        except SQLAlchemyError:
            logger.exception("Could not persist authenticated user profile")
            persistence["postgres"] = {"health": "error"}
    else:
        persistence["postgres"] = {"health": "not_configured"}

    if redis.enabled:
        try:
            expires_at = int(claims.get("exp", 0))
            ttl = max(1, min(expires_at - int(time.time()), 3600))
            await redis.set_json(
                f"session:{subject}",
                {
                    "subject": subject,
                    "provider": "firebase",
                    "role": role,
                    "expires_at": expires_at,
                },
                ttl_seconds=ttl,
            )
            persistence["redis"] = "stored"
        except (RedisError, TypeError, ValueError):
            logger.exception("Could not cache authenticated session")
            persistence["redis"] = "error"
    else:
        persistence["redis"] = "not_configured"

    degraded = any(
        value.get("health") != "healthy"
        if isinstance(value, dict)
        else value in {"error", "not_configured"}
        for value in persistence.values()
    )
    return {
        "success": True,
        "subject": subject,
        "role": role,
        "persistence": persistence,
        "persistence_degraded": degraded,
    }

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPIError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..dependencies import current_subject
from ..schemas import UserPreferencesPayload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/preferences", tags=["preferences"])
DEFAULT_PREFERENCES = {
    "theme": "light",
    "news_lang": "en-US",
    "news_voice_digest_enabled": True,
    "chart_timeframe": "15m",
}


async def _load_preferences(request: Request, subject: str) -> dict[str, Any]:
    redis = request.app.state.redis
    if redis.enabled:
        try:
            cached = await redis.get_json(f"preferences:{subject}")
            if isinstance(cached, dict):
                return {**DEFAULT_PREFERENCES, **cached}
        except (RedisError, ValueError):
            logger.exception("Could not read cached UI preferences")

    database = request.app.state.database
    if database.enabled:
        try:
            record = await database.get_user_preferences(subject)
            if record:
                return {**DEFAULT_PREFERENCES, **record.preferences}
        except (SQLAlchemyError, RuntimeError):
            logger.exception("Could not read relational UI preferences")

    firebase = request.app.state.firebase
    if firebase.enabled:
        try:
            preferences = await firebase.get_preferences(subject)
            if preferences:
                return {**DEFAULT_PREFERENCES, **preferences}
        except (FirebaseError, GoogleAPIError, RuntimeError):
            logger.exception("Could not read Firestore UI preferences")
    return dict(DEFAULT_PREFERENCES)


@router.get("")
async def get_preferences(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    return {"success": True, "preferences": await _load_preferences(request, subject)}


@router.put("")
async def save_preferences(
    payload: UserPreferencesPayload,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    current = await _load_preferences(request, subject)
    current.update(payload.model_dump(exclude_none=True))
    persistence: dict[str, Any] = {}
    durable_write = False

    database = request.app.state.database
    if database.enabled:
        try:
            status = await database.upsert_user_preferences(subject, current)
            persistence["postgres"] = status.as_dict()
            durable_write = status.primary_applied or status.backup_applied
        except (SQLAlchemyError, RuntimeError):
            logger.exception("Could not persist UI preferences to PostgreSQL")
            persistence["postgres"] = {"health": "error"}
    else:
        persistence["postgres"] = {"health": "not_configured"}

    redis = request.app.state.redis
    if redis.enabled:
        try:
            await redis.set_json(f"preferences:{subject}", current, ttl_seconds=86400)
            persistence["redis"] = "stored"
            durable_write = True
        except (RedisError, ValueError):
            logger.exception("Could not cache UI preferences")
            persistence["redis"] = "error"
    else:
        persistence["redis"] = "not_configured"

    firebase = request.app.state.firebase
    if firebase.enabled:
        try:
            await firebase.upsert_preferences(subject, current)
            persistence["firebase"] = "stored"
            durable_write = True
        except (FirebaseError, GoogleAPIError, RuntimeError):
            logger.exception("Could not persist UI preferences to Firestore")
            persistence["firebase"] = "error"
    else:
        persistence["firebase"] = "not_configured"

    if not durable_write:
        raise HTTPException(status_code=503, detail={"message": "No preference store accepted the update", "persistence": persistence})
    return {"success": True, "preferences": current, "persistence": persistence}

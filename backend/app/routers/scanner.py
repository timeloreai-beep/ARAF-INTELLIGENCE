import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..dependencies import current_subject
from ..schemas import ScannerRequest
from ..services.scanner import UniverseScanner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scanner", tags=["scanner"])


async def _persist_scan(request: Request, subject: str, market: str, items: list[dict]) -> dict:
    database = request.app.state.database
    mongo = request.app.state.mongo
    redis = request.app.state.redis
    created_at = datetime.now(timezone.utc)
    persistence: dict = {}

    if mongo.enabled:
        try:
            await mongo.insert(
                "scanner_history",
                {
                    "subject": subject,
                    "market": market,
                    "count": len(items),
                    "items": items,
                    "created_at": created_at,
                },
            )
            persistence["mongodb"] = "stored"
        except PyMongoError:
            logger.exception("Could not persist scanner results to MongoDB")
            persistence["mongodb"] = "error"
    else:
        persistence["mongodb"] = "not_configured"

    if database.enabled:
        try:
            status = await database.add_audit_event(
                actor_id=subject,
                event_type="scanner.completed",
                resource_type="scanner",
                resource_id=market,
                action="scan",
                outcome="success",
                payload={"market": market, "count": len(items)},
            )
            persistence["postgres"] = status.as_dict()
        except SQLAlchemyError:
            logger.exception("Could not persist scanner audit event")
            persistence["postgres"] = {"health": "error"}
    else:
        persistence["postgres"] = {"health": "not_configured"}

    if redis.enabled:
        try:
            await redis.set_json(
                f"scanner:last:{subject}:{market.lower()}",
                {"market": market, "count": len(items), "created_at": created_at.isoformat()},
                ttl_seconds=60,
            )
            persistence["redis"] = "cached"
        except (RedisError, ValueError):
            logger.exception("Could not cache scanner summary")
            persistence["redis"] = "error"
    else:
        persistence["redis"] = "not_configured"
    return persistence


def _persistence_degraded(persistence: dict) -> bool:
    for value in persistence.values():
        if isinstance(value, dict):
            if value.get("health") not in {"healthy"}:
                return True
        elif isinstance(value, str) and value in {
            "error",
            "not_configured",
            "partial",
            "degraded",
            "offline",
        }:
            return True
    return False


@router.post("/india")
async def scan_india(
    payload: ScannerRequest,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    if payload.exchange == "CRYPTO":
        raise HTTPException(status_code=422, detail="Use /scanner/crypto for crypto scans")
    items = await UniverseScanner().scan_india_tradingview(payload.limit)
    if payload.eligible_only:
        items = [item for item in items if item["eligible"]]
    if payload.exchange in {"NSE", "BSE"}:
        items = [item for item in items if item["exchange"] == payload.exchange]
    persistence = await _persist_scan(request, subject, "INDIA", items)
    return {
        "success": True,
        "count": len(items),
        "items": items,
        "persistence": persistence,
        "persistence_degraded": _persistence_degraded(persistence),
    }


@router.post("/crypto")
async def scan_crypto(
    payload: ScannerRequest,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    try:
        items = await UniverseScanner().scan_crypto(payload.limit)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Crypto market data sources are unavailable",
        ) from exc
    if payload.eligible_only:
        items = [item for item in items if item["eligible"]]
    persistence = await _persist_scan(request, subject, "CRYPTO", items)
    return {
        "success": True,
        "count": len(items),
        "items": items,
        "persistence": persistence,
        "persistence_degraded": _persistence_degraded(persistence),
    }

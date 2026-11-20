import hashlib
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from ..dependencies import current_subject, get_risk_guardian
from ..schemas import OrderRequest
from ..services.risk import RiskGuardian
from ..quant.confluence import evaluate_five_layers
from ..brokers.shoonya import ShoonyaAdapter
from ..brokers.coindcx import CoinDCXAdapter
from ..services.risk_persistence import (
    is_persistence_degraded,
    load_risk_state,
    persist_risk_state,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])
_idem: dict[str, dict] = {}


@router.post("/place")
async def place_order(
    payload: OrderRequest,
    request: Request,
    subject: str = Depends(current_subject),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    request_digest = hashlib.sha256(
        json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    idempotency_key = f"{subject}:{payload.client_order_id}"
    if idempotency_key in _idem:
        cached = _idem[idempotency_key]
        if cached["request_digest"] != request_digest:
            raise HTTPException(status_code=409, detail="Client order ID was already used with different parameters")
        return cached["result"]

    database = request.app.state.database
    redis = request.app.state.redis
    mongo = request.app.state.mongo
    redis_key = f"idempotency:order:{idempotency_key}"
    if redis.enabled:
        try:
            cached = await redis.get_json(redis_key)
            if isinstance(cached, dict) and cached.get("request_digest"):
                if cached["request_digest"] != request_digest:
                    raise HTTPException(
                        status_code=409,
                        detail="Client order ID was already used with different parameters",
                    )
                _idem[idempotency_key] = cached
                return cached["result"]
        except (RedisError, ValueError):
            logger.exception("Could not read order idempotency cache")

    if database.enabled:
        try:
            existing = await database.get_order(subject, payload.client_order_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Order idempotency check is unavailable") from exc
        if existing:
            existing_payload = existing.payload or {}
            if existing_payload.get("request_digest") != request_digest:
                raise HTTPException(status_code=409, detail="Client order ID was already used with different parameters")
            prior_result = existing_payload.get("response")
            if isinstance(prior_result, dict):
                _idem[idempotency_key] = {
                    "request_digest": request_digest,
                    "result": prior_result,
                }
                return prior_result

    if payload.mode == "LIVE" and not get_settings().live_trading_enabled:
        raise HTTPException(status_code=403, detail="Live trading is disabled; paper mode only")

    risk_persistence: dict = {}
    if payload.operation == "ENTRY":
        if payload.stop_loss is None:
            raise HTTPException(status_code=400, detail="Stop-loss is mandatory")
        confluence = evaluate_five_layers(
            macro_green=payload.macro_green,
            smc_green=payload.smc_green,
            mtf_green=payload.mtf_green,
            volume_green=payload.volume_green,
            risk_green=True,
        )
        if payload.mode == "LIVE" and not confluence.allowed:
            raise HTTPException(
                status_code=403,
                detail={"code": "FIVE_LAYER_GATE_REJECTED", "reasons": confluence.reasons},
            )

        try:
            await load_risk_state(request, subject, guardian)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
        entry_price = float(payload.entry_price or 0)
        allowed, reason, state = guardian.precheck_entry(
            subject=subject,
            entry_price=entry_price,
            stop_loss=float(payload.stop_loss),
            quantity=float(payload.quantity),
            is_auto_trade=False,
        )
        risk_persistence = await persist_risk_state(request, subject, state)
        if not allowed:
            raise HTTPException(status_code=403, detail={"code": "RISK_BLOCKED", "reason": reason})

    result = {
        "success": True,
        "mode": payload.mode,
        "state": "PAPER_FILLED" if payload.mode == "PAPER" else "ACCEPTED",
        "client_order_id": payload.client_order_id,
        "protection_status": "SIMULATED_SL" if payload.mode == "PAPER" else "BROKER_PROTECTION_REQUIRED",
    }
    if payload.mode == "LIVE":
        try:
            if payload.exchange == "COINDCX" or payload.symbol.upper().endswith("USDT"):
                adapter = CoinDCXAdapter()
                broker_result = await adapter.place_order(
                    symbol=f"B-{payload.symbol.upper().replace('USDT', '_USDT')}",
                    side=payload.side,
                    quantity=payload.quantity,
                    stop_loss=payload.stop_loss,
                    take_profit=payload.take_profit,
                    client_order_id=payload.client_order_id,
                    leverage=1,
                )
            else:
                adapter = ShoonyaAdapter()
                broker_result = await adapter.place_order(
                    exchange=payload.exchange,
                    symbol=payload.symbol,
                    side=payload.side,
                    quantity=int(payload.quantity),
                )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "BROKER_NOT_CONFIGURED", "reason": str(exc)})
        if not broker_result.accepted:
            raise HTTPException(
                status_code=502,
                detail={"code": "BROKER_REJECTED", "reason": broker_result.message or broker_result.status},
            )
        result.update({
            "broker_order_id": broker_result.broker_order_id,
            "state": broker_result.status,
            "protection_status": "BROKER_SL_TP_SUBMITTED",
        })

    persistence: dict = {}
    if database.enabled:
        try:
            order_status = await database.upsert_order(
                user_id=subject,
                client_order_id=payload.client_order_id,
                broker_order_id=result.get("broker_order_id"),
                symbol=payload.symbol,
                exchange=payload.exchange,
                side=payload.side,
                operation=payload.operation,
                mode=payload.mode,
                quantity=payload.quantity,
                entry_price=payload.entry_price,
                stop_loss=payload.stop_loss,
                take_profit=payload.take_profit,
                state=result["state"],
                protection_status=result["protection_status"],
                payload={"request_digest": request_digest, "response": result},
            )
            persistence["postgres"] = order_status.as_dict()
            if not (order_status.primary_applied or order_status.backup_applied):
                raise HTTPException(status_code=503, detail="Order could not be persisted to either relational database")
            audit_status = await database.add_audit_event(
                actor_id=subject,
                event_type="order.submitted",
                resource_type="order",
                resource_id=payload.client_order_id,
                action="create",
                outcome="success",
                payload={"symbol": payload.symbol, "mode": payload.mode, "state": result["state"]},
            )
            persistence["postgres_audit"] = audit_status.as_dict()
        except SQLAlchemyError as exc:
            logger.exception("Could not persist order")
            raise HTTPException(status_code=503, detail="Order persistence failed") from exc
    else:
        persistence["postgres"] = {"health": "not_configured"}

    if mongo.enabled:
        try:
            await mongo.insert(
                "audit_events",
                {
                    "subject": subject,
                    "event_type": "order.submitted",
                    "resource_id": payload.client_order_id,
                    "payload": {
                        "symbol": payload.symbol,
                        "mode": payload.mode,
                        "state": result["state"],
                    },
                    "created_at": datetime.now(timezone.utc),
                },
            )
            persistence["mongodb"] = "stored"
        except PyMongoError:
            logger.exception("Could not persist order audit history to MongoDB")
            persistence["mongodb"] = "error"
    else:
        persistence["mongodb"] = "not_configured"

    if risk_persistence:
        persistence["risk"] = risk_persistence
    response = {
        **result,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }
    cached = {"request_digest": request_digest, "result": response}
    _idem[idempotency_key] = cached
    if len(_idem) > 10000:
        _idem.pop(next(iter(_idem)))
    if redis.enabled:
        try:
            await redis.set_json(redis_key, cached, ttl_seconds=86400)
        except (RedisError, ValueError):
            logger.exception("Could not cache order idempotency result")
            response["persistence_degraded"] = True
            response["persistence"]["redis"] = "error"
    else:
        response["persistence"]["redis"] = "not_configured"
        response["persistence_degraded"] = True
    return response

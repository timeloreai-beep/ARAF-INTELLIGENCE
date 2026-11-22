import asyncio
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
from ..services.risk_persistence import (
    is_persistence_degraded,
    load_risk_state,
    persist_risk_state,
)
from ..services.automation import trading_day_ist

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])
_idem: dict[str, dict] = {}
APPROVED_KYC_STATUSES = {"APPROVED", "VERIFIED_COMPLIANT"}


async def _release_order_reservation(
    request: Request,
    subject: str,
    client_order_id: str,
    request_digest: str,
) -> None:
    idempotency_key = f"{subject}:{client_order_id}"
    redis_key = f"idempotency:order:{idempotency_key}"
    redis = request.app.state.redis
    cached = _idem.get(idempotency_key)
    if cached and cached.get("request_digest") == request_digest:
        _idem.pop(idempotency_key, None)
    if redis.enabled:
        try:
            await redis.delete(redis_key)
        except RedisError:
            logger.exception("Could not release order idempotency reservation")


async def _sync_subscription_access(
    request: Request,
    subject: str,
    guardian: RiskGuardian,
) -> None:
    database = request.app.state.database
    get_app_user = getattr(database, "get_app_user", None)
    if not database.enabled or not callable(get_app_user):
        return
    try:
        user = await get_app_user(subject)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.exception("Could not verify subscription access for order entry")
        raise HTTPException(
            status_code=503, detail="Subscription access verification is unavailable"
        ) from exc

    if user is None:
        guardian.sync_user_access(subject, True)
        return

    guardian.sync_user_access(
        subject,
        bool(
            user.is_active
            and user.kyc_status in APPROVED_KYC_STATUSES
            and (
                user.is_vip_free
                or (
                    user.subscription_expires_at is not None
                    and (
                        user.subscription_expires_at.replace(tzinfo=timezone.utc)
                        if user.subscription_expires_at.tzinfo is None
                        else user.subscription_expires_at.astimezone(timezone.utc)
                    )
                    > datetime.now(timezone.utc)
                )
            )
        ),
    )
    if not guardian.user_access_allowed(subject):
        if not user.is_active:
            code = "USER_ACCESS_REVOKED"
        elif user.kyc_status not in APPROVED_KYC_STATUSES:
            code = "USER_KYC_NOT_APPROVED"
        else:
            code = "SUBSCRIPTION_EXPIRED"
        raise HTTPException(status_code=403, detail={"code": code})


@router.post("/place")
async def place_order(
    payload: OrderRequest,
    request: Request,
    subject: str = Depends(current_subject),
    guardian: RiskGuardian = Depends(get_risk_guardian),
) -> dict:
    return await _place_order(payload, request, subject, guardian)


async def _place_order_unlocked(
    payload: OrderRequest,
    request: Request,
    subject: str,
    guardian: RiskGuardian,
    *,
    trusted_opportunity: dict | None = None,
) -> dict:
    request_digest = hashlib.sha256(
        json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    idempotency_key = f"{subject}:{payload.client_order_id}"
    if idempotency_key in _idem:
        cached = _idem[idempotency_key]
        if cached["request_digest"] != request_digest:
            raise HTTPException(status_code=409, detail="Client order ID was already used with different parameters")
        if cached.get("status") == "IN_PROGRESS":
            raise HTTPException(status_code=409, detail="Client order ID is currently being processed")
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
                if cached.get("status") == "IN_PROGRESS":
                    raise HTTPException(status_code=409, detail="Client order ID is currently being processed")
                _idem[idempotency_key] = cached
                if isinstance(cached.get("result"), dict):
                    return cached["result"]
        except (RedisError, ValueError):
            logger.exception("Could not read order idempotency cache")
        try:
            reserved = await redis.set_if_absent(
                redis_key,
                json.dumps(
                    {"request_digest": request_digest, "status": "IN_PROGRESS"},
                    separators=(",", ":"),
                ),
                ttl_seconds=300,
            )
            if not reserved:
                raise HTTPException(status_code=409, detail="Client order ID is already reserved")
        except RedisError:
            logger.exception("Could not reserve order idempotency key")
            raise HTTPException(status_code=503, detail="Order idempotency reservation is unavailable")

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

    _idem[idempotency_key] = {
        "request_digest": request_digest,
        "status": "IN_PROGRESS",
    }

    if payload.mode == "LIVE" and not get_settings().live_trading_enabled:
        raise HTTPException(status_code=403, detail="Live trading is disabled; paper mode only")
    if payload.mode == "LIVE":
        await _release_order_reservation(
            request,
            subject,
            payload.client_order_id,
            request_digest,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LIVE_BROKER_PROTECTION_UNAVAILABLE",
                "reason": "Exchange-native stop-loss, take-profit, live exit, and position reconciliation are not verified",
            },
        )

    if payload.operation == "ENTRY":
        await _sync_subscription_access(request, subject, guardian)

    risk_persistence: dict = {}
    risk_state = None
    auto_budget_day: str | None = None
    auto_budget_amount = 0.0
    auto_budget_limit = 0.0
    auto_budget_reserved = False
    if payload.operation == "ENTRY":
        if payload.mode == "PAPER" and not database.enabled:
            raise HTTPException(
                status_code=503,
                detail={"code": "PAPER_POSITION_PERSISTENCE_UNAVAILABLE"},
            )
        control = None
        if database.enabled:
            try:
                control = await database.get_admin_control()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail="Global trade controls are unavailable") from exc
            if control and control.global_kill_switch:
                raise HTTPException(status_code=403, detail={"code": "GLOBAL_KILL_SWITCH_ACTIVE"})
        if payload.stop_loss is None:
            raise HTTPException(status_code=400, detail="Stop-loss is mandatory")
        if payload.entry_price is None or payload.take_profit is None:
            raise HTTPException(status_code=400, detail="Entry, stop-loss, and target are mandatory")
        risk_distance = abs(float(payload.entry_price) - float(payload.stop_loss))
        reward_distance = abs(float(payload.take_profit) - float(payload.entry_price))
        if (
            risk_distance <= 0
            or reward_distance + 1e-12 < 2 * risk_distance
            or (payload.side == "BUY" and not (
                payload.stop_loss < payload.entry_price < payload.take_profit
            ))
            or (payload.side == "SELL" and not (
                payload.take_profit < payload.entry_price < payload.stop_loss
            ))
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "MINIMUM_RISK_REWARD_NOT_MET", "minimum_rr": 2},
            )
        confluence = evaluate_five_layers(
            macro_green=payload.macro_green,
            smc_green=payload.smc_green,
            mtf_green=payload.mtf_green,
            volume_green=payload.volume_green,
            risk_green=True,
        )
        if (payload.mode == "LIVE" or payload.is_auto_trade) and not confluence.allowed:
            raise HTTPException(
                status_code=403,
                detail={"code": "FIVE_LAYER_GATE_REJECTED", "reasons": confluence.reasons},
            )
        if payload.is_auto_trade:
            if payload.mode != "PAPER":
                raise HTTPException(status_code=403, detail="Automated execution is paper-only")
            if not database.enabled:
                raise HTTPException(status_code=503, detail="Persisted auto-trade control is unavailable")
            if not control or not control.auto_trade_enabled or control.global_kill_switch:
                raise HTTPException(status_code=403, detail={"code": "AUTO_TRADE_DISABLED"})
            if not payload.automation_opportunity_id:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "SERVER_OPPORTUNITY_REQUIRED"},
                )
            hub = getattr(request.app.state, "opportunity_hub", None)
            if hub is None:
                raise HTTPException(status_code=503, detail="Automation signal service is unavailable")
            if not getattr(request.app.state, "paper_lifecycle_ready", False):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "PAPER_POSITION_LIFECYCLE_UNAVAILABLE"},
                )
            opportunity = await hub.claim(subject, payload.automation_opportunity_id)
            if (
                not opportunity
                or not opportunity.get("autoTradeEligible")
                or not opportunity.get("executionEligible")
                or int(opportunity.get("fusionScore", 0)) < 90
            ):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "SERVER_AUTO_TRADE_GATE_REJECTED"},
                )
        else:
            opportunity = trusted_opportunity
            if (
                not opportunity
                or not opportunity.get("serverGatesPassed")
                or not opportunity.get("executionEligible")
                or int(opportunity.get("fusionScore", 0)) < 70
            ):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "SERVER_CONFLUENCE_OPPORTUNITY_REQUIRED"},
                )
            if not getattr(request.app.state, "paper_lifecycle_ready", False):
                raise HTTPException(
                    status_code=403,
                    detail={"code": "PAPER_POSITION_LIFECYCLE_UNAVAILABLE"},
                )
        expected_values = (
            (payload.symbol, opportunity.get("orderKey")),
            (payload.exchange, opportunity.get("exchange")),
            (payload.side, opportunity.get("side")),
        )
        if any(actual != expected for actual, expected in expected_values) or any(
            abs(float(actual or 0) - float(expected or 0)) > 1e-8
            for actual, expected in (
                (payload.quantity, opportunity.get("quantity")),
                (payload.entry_price, opportunity.get("entry")),
                (payload.stop_loss, opportunity.get("sl")),
                (payload.take_profit, opportunity.get("target")),
            )
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "ORDER_DOES_NOT_MATCH_SERVER_OPPORTUNITY"},
            )
        if (
            payload.broker_token != opportunity.get("brokerToken")
            or payload.trailing_distance is None
            or abs(
                float(payload.trailing_distance)
                - float(opportunity.get("trailingDistance") or 0)
            ) > 1e-8
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "ORDER_DOES_NOT_MATCH_SERVER_OPPORTUNITY"},
            )
        if payload.is_auto_trade:
            settings = await hub.get_settings(subject)
            if not settings.get("enabled"):
                raise HTTPException(status_code=403, detail={"code": "USER_AUTO_TRADE_DISABLED"})
            auto_budget_day = trading_day_ist()
            auto_budget_amount = float(opportunity.get("capitalUsedInr") or 0)
            auto_budget_limit = float(settings.get("dailyBudgetInr") or 0)
            if auto_budget_amount <= 0 or auto_budget_limit <= 0:
                raise HTTPException(status_code=403, detail={"code": "INVALID_AUTO_TRADE_BUDGET"})
            news_engine = getattr(request.app.state, "news_engine", None)
            if news_engine and news_engine.priority_guard_active:
                raise HTTPException(status_code=403, detail={"code": "PRIORITY_NEWS_GUARD_ACTIVE"})

        try:
            await load_risk_state(request, subject, guardian)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
        if payload.is_auto_trade:
            try:
                auto_budget_reserved = await database.reserve_auto_trade_budget(
                    subject,
                    auto_budget_day or trading_day_ist(),
                    auto_budget_limit,
                    auto_budget_amount,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail="Auto-trade budget is unavailable") from exc
            if not auto_budget_reserved:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "DAILY_AUTO_TRADE_BUDGET_EXHAUSTED"},
                )
        entry_price = float(payload.entry_price or 0)
        allowed, reason, state = guardian.precheck_entry(
            subject=subject,
            entry_price=entry_price,
            stop_loss=float(payload.stop_loss),
            quantity=float(payload.quantity),
            is_auto_trade=payload.is_auto_trade,
            side=payload.side,
        )
        try:
            risk_persistence = await persist_risk_state(request, subject, state)
        except RuntimeError as exc:
            state = guardian.rollback_entry_reservation(subject)
            try:
                await persist_risk_state(request, subject, state)
            except RuntimeError:
                logger.exception("Could not roll back risk trade reservation")
            if auto_budget_reserved:
                try:
                    await database.settle_auto_trade_budget(
                        subject,
                        auto_budget_day or trading_day_ist(),
                        auto_budget_amount,
                        commit=False,
                    )
                except RuntimeError:
                    logger.exception("Could not release auto-trade budget after risk-state persistence failure")
            raise HTTPException(status_code=503, detail="Risk state persistence is unavailable") from exc
        if not allowed:
            if auto_budget_reserved:
                try:
                    await database.settle_auto_trade_budget(
                        subject,
                        auto_budget_day or trading_day_ist(),
                        auto_budget_amount,
                        commit=False,
                    )
                    auto_budget_reserved = False
                except RuntimeError:
                    logger.exception("Could not release auto-trade budget after risk rejection")
            raise HTTPException(status_code=403, detail={"code": "RISK_BLOCKED", "reason": reason})
        risk_state = guardian.to_record(state)

    result = {
        "success": True,
        "mode": payload.mode,
        "state": "PAPER_FILLED" if payload.mode == "PAPER" else "ACCEPTED",
        "client_order_id": payload.client_order_id,
        "protection_status": "SIMULATED_SL" if payload.mode == "PAPER" else "BROKER_PROTECTION_REQUIRED",
    }

    persistence: dict = {}
    if database.enabled:
        try:
            order_values = {
                "user_id": subject,
                "client_order_id": payload.client_order_id,
                "broker_order_id": result.get("broker_order_id"),
                "symbol": payload.symbol,
                "exchange": payload.exchange,
                "side": payload.side,
                "operation": payload.operation,
                "mode": payload.mode,
                "quantity": payload.quantity,
                "entry_price": payload.entry_price,
                "stop_loss": payload.stop_loss,
                "take_profit": payload.take_profit,
                "state": result["state"],
                "protection_status": result["protection_status"],
                "payload": {"request_digest": request_digest, "response": result},
            }
            if payload.mode == "PAPER" and payload.operation == "ENTRY":
                entry_price = float(payload.entry_price or 0)
                position_status = await database.upsert_paper_entry(
                    order_values=order_values,
                    position_values={
                        "user_id": subject,
                        "client_order_id": payload.client_order_id,
                        "symbol": payload.symbol,
                        "exchange": payload.exchange,
                        "broker_token": payload.broker_token,
                        "side": payload.side,
                        "quantity": payload.quantity,
                        "entry_price": entry_price,
                        "stop_loss": float(payload.stop_loss or 0),
                        "take_profit": float(payload.take_profit or 0),
                        "trailing_distance": float(
                            payload.trailing_distance
                            or abs(entry_price - float(payload.stop_loss or 0)) / 1.5
                        ),
                        "favorable_price": entry_price,
                        "pnl_fx_to_inr": float(opportunity.get("fxToInr") or 1),
                        "status": "OPEN",
                        "is_auto_trade": payload.is_auto_trade,
                        "realized_pnl": 0,
                    },
                )
                order_status = position_status
                if not (position_status.primary_applied or position_status.backup_applied):
                    raise HTTPException(status_code=503, detail="Paper entry and position could not be persisted")
                persistence["paper_position"] = "opened"
            else:
                order_status = await database.upsert_order(**order_values)
        except SQLAlchemyError as exc:
            logger.exception("Could not persist order")
            state = guardian.rollback_entry_reservation(subject)
            try:
                await persist_risk_state(request, subject, state)
            except RuntimeError:
                logger.exception("Could not roll back risk trade reservation after persistence failure")
            if auto_budget_reserved:
                try:
                    await database.settle_auto_trade_budget(
                        subject,
                        auto_budget_day or trading_day_ist(),
                        auto_budget_amount,
                        commit=False,
                    )
                except RuntimeError:
                    logger.exception("Could not release auto-trade budget after order persistence failure")
            raise HTTPException(status_code=503, detail="Order persistence failed") from exc
        persistence["postgres"] = order_status.as_dict()
        if not (order_status.primary_applied or order_status.backup_applied):
            if auto_budget_reserved:
                try:
                    await database.settle_auto_trade_budget(
                        subject,
                        auto_budget_day or trading_day_ist(),
                        auto_budget_amount,
                        commit=False,
                    )
                except RuntimeError:
                    logger.exception("Could not release auto-trade budget after rejected persistence")
            if risk_state:
                state = guardian.rollback_entry_reservation(subject)
                try:
                    await persist_risk_state(request, subject, state)
                except RuntimeError:
                    logger.exception("Could not roll back risk trade reservation after rejected persistence")
            raise HTTPException(status_code=503, detail="Order could not be persisted to either relational database")
        if auto_budget_reserved:
            try:
                settled = await database.settle_auto_trade_budget(
                    subject,
                    auto_budget_day or trading_day_ist(),
                    auto_budget_amount,
                    commit=True,
                )
                persistence["auto_trade_budget"] = "committed" if settled else "reservation_missing"
                if not settled:
                    logger.error("Auto-trade budget reservation disappeared before commit for user %s", subject)
            except RuntimeError:
                logger.exception("Could not commit auto-trade budget usage for user %s", subject)
                persistence["auto_trade_budget"] = "settlement_error"
        try:
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
            logger.exception("Could not persist order audit event")
            raise HTTPException(status_code=503, detail="Order audit persistence failed") from exc
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
        "risk": risk_state,
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


async def _place_order(
    payload: OrderRequest,
    request: Request,
    subject: str,
    guardian: RiskGuardian,
    *,
    trusted_opportunity: dict | None = None,
) -> dict:
    database = request.app.state.database
    order_locks = getattr(request.app.state, "order_idempotency_locks", None)
    if order_locks is None:
        order_locks = {}
        request.app.state.order_idempotency_locks = order_locks
    idempotency_lock = order_locks.setdefault(
        (subject, payload.client_order_id),
        asyncio.Lock(),
    )
    async with idempotency_lock:
        return await _place_order_locked(
            payload,
            request,
            subject,
            guardian,
            trusted_opportunity=trusted_opportunity,
        )


async def _place_order_locked(
    payload: OrderRequest,
    request: Request,
    subject: str,
    guardian: RiskGuardian,
    *,
    trusted_opportunity: dict | None = None,
) -> dict:
    database = request.app.state.database
    if (
        payload.operation != "ENTRY"
        or payload.mode != "PAPER"
        or not database.enabled
    ):
        return await _place_order_unlocked(
            payload,
            request,
            subject,
            guardian,
            trusted_opportunity=trusted_opportunity,
        )

    locks = getattr(request.app.state, "paper_setup_locks", None)
    if locks is None:
        locks = {}
        request.app.state.paper_setup_locks = locks
    setup = (subject, payload.symbol, payload.exchange, payload.side)
    lock = locks.setdefault(setup, asyncio.Lock())
    async with lock:
        has_manual_exit_block = getattr(database, "has_manual_exit_block", None)
        if has_manual_exit_block is not None and await has_manual_exit_block(
            subject, payload.symbol, payload.exchange, payload.side
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "MANUAL_EXIT_REENTRY_LOCK"},
            )
        return await _place_order_unlocked(
            payload,
            request,
            subject,
            guardian,
            trusted_opportunity=trusted_opportunity,
        )

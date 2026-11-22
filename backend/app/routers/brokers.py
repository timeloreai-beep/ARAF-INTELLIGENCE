from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from pymongo.errors import PyMongoError

from ..brokers.coindcx import CoinDCXAdapter
from ..brokers.shoonya import ShoonyaAdapter
from ..dependencies import current_subject
from ..security import decrypt_broker_session, encrypt_broker_session
from ..services.risk_persistence import is_persistence_degraded

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brokers", tags=["brokers"])


class ShoonyaQuoteRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    exchange: Literal["NSE", "BSE", "NFO", "BFO"]


class CoinDCXQuoteRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_/-]+$")


def _quote_dict(quote, requested_symbol: str) -> dict:
    return {
        "symbol": quote.symbol or requested_symbol,
        "exchange": quote.exchange,
        "price": quote.price,
        "high": quote.high,
        "low": quote.low,
        "volume": quote.volume,
        "source": quote.source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_position(row: dict, broker: str) -> dict:
    symbol = str(row.get("tsym") or row.get("pair") or row.get("symbol") or row.get("token") or "")
    exchange = (
        broker
        if broker == "COINDCX"
        else str(row.get("exch") or row.get("exchange") or broker).upper()
    )
    quantity = float(row.get("netqty", row.get("active_pos", row.get("quantity", 0))) or 0)
    average_price = float(
        row.get("netavgprc", row.get("avg_price", row.get("average_price", 0))) or 0
    )
    unrealized_pnl = float(row.get("urmtom", row.get("unrealized_pnl", 0)) or 0)
    realized_pnl = float(row.get("rpnl", row.get("realized_pnl", 0)) or 0)
    if not symbol or not all(
        math.isfinite(value)
        for value in (quantity, average_price, unrealized_pnl, realized_pnl)
    ):
        raise ValueError("Broker position contains invalid instrument or numeric data")
    return {
        "symbol": symbol,
        "exchange": exchange,
        "side": "LONG" if quantity > 0 else "SHORT" if quantity < 0 else "FLAT",
        "quantity": abs(quantity),
        "average_price": average_price or None,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl,
        "is_open": quantity != 0,
        "payload": row,
    }


async def _persist_positions(
    request: Request,
    subject: str,
    broker: str,
    positions: list[dict],
) -> dict:
    database = request.app.state.database
    mongo = request.app.state.mongo
    persistence: dict = {}

    if database.enabled:
        exchanges = {"COINDCX"} if broker == "COINDCX" else {"NSE", "BSE", "NFO", "BFO"}
        existing = []
        for exchange in exchanges:
            existing.extend(await database.list_positions(subject, exchange))

        normalized = [_normalize_position(row, broker) for row in positions]
        current_keys = {(item["symbol"], item["exchange"]) for item in normalized}
        statuses = []
        for item in normalized:
            status = await database.upsert_position(user_id=subject, **item)
            statuses.append(status)
        for old in existing:
            key = (old.symbol, old.exchange)
            if old.is_open and key not in current_keys:
                status = await database.upsert_position(
                    user_id=subject,
                    symbol=old.symbol,
                    exchange=old.exchange,
                    side="FLAT",
                    quantity=0,
                    average_price=old.average_price,
                    unrealized_pnl=0,
                    realized_pnl=old.realized_pnl,
                    is_open=False,
                    payload={"sync": "broker_snapshot_absent", "broker": broker},
                )
                statuses.append(status)
        persistence["postgres"] = {
            "health": (
                "offline"
                if statuses and not any(s.primary_applied or s.backup_applied for s in statuses)
                else "healthy"
                if not statuses or all(s.replicated for s in statuses)
                else "partial"
            ),
            "records": len(statuses),
            "writes": [status.as_dict() for status in statuses],
        }
    else:
        normalized = [_normalize_position(row, broker) for row in positions]
        persistence["postgres"] = {"health": "not_configured"}

    if mongo.enabled:
        try:
            await mongo.insert(
                "position_snapshots",
                {
                    "subject": subject,
                    "broker": broker,
                    "positions": positions,
                    "created_at": datetime.now(timezone.utc),
                },
            )
            persistence["mongodb"] = "stored"
        except PyMongoError:
            logger.exception("Could not persist broker position history")
            persistence["mongodb"] = "error"
    else:
        persistence["mongodb"] = "not_configured"
    return persistence


async def _cached_quote(request: Request, key: str) -> dict | None:
    redis = request.app.state.redis
    if not redis.enabled:
        return None
    try:
        return await redis.get_json(key)
    except (RedisError, ValueError):
        logger.exception("Could not read broker quote cache")
        return None


async def _persist_quote(
    request: Request,
    subject: str,
    record: dict,
    cache_key: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    database = request.app.state.database
    mongo = request.app.state.mongo
    redis = request.app.state.redis
    document = {"subject": subject, **record}

    if database.enabled:
        try:
            status = await database.persist_quote(subject, record)
            result["postgres"] = status.health
            result["postgres_replication"] = status.as_dict()
        except SQLAlchemyError:
            logger.exception("Could not persist broker quote to relational database")
            result["postgres"] = "error"
    else:
        result["postgres"] = "not_configured"

    if mongo.enabled:
        try:
            await mongo.insert("market_quotes", document)
            result["mongodb"] = "stored"
        except PyMongoError:
            logger.exception("Could not persist broker quote history to MongoDB")
            result["mongodb"] = "error"
    else:
        result["mongodb"] = "not_configured"

    if redis.enabled:
        try:
            result["redis"] = "cached"
            await redis.set_json(
                cache_key,
                {"quote": record, "persistence": result},
                ttl_seconds=3,
            )
        except (RedisError, ValueError):
            logger.exception("Could not cache broker quote")
            result["redis"] = "error"
    else:
        result["redis"] = "not_configured"
    return result


async def _quote_response(request: Request, subject: str, record: dict, cache_key: str) -> dict:
    persistence = await _persist_quote(request, subject, record, cache_key)
    failed = any(
        value in {"error", "not_configured", "partial", "degraded", "offline"}
        for value in persistence.values()
        if isinstance(value, str)
    ) or bool(persistence.get("postgres_replication", {}).get("degraded"))
    return {
        "success": True,
        "read_only": True,
        "quote": record,
        "persistence": persistence,
        "persistence_degraded": failed,
    }


async def _shoonya_adapter(request: Request, subject: str) -> ShoonyaAdapter:
    redis = request.app.state.redis
    adapter = ShoonyaAdapter(redis=redis)
    if not redis.enabled:
        return adapter
    try:
        cached = await redis.get_json(f"broker:session:shoonya:{subject}")
    except (RedisError, ValueError) as exc:
        logger.warning("Could not read Shoonya session cache: %s", type(exc).__name__)
        return adapter
    if (
        isinstance(cached, dict)
        and cached.get("user_id") == adapter.user_id
        and isinstance(cached.get("session_token"), str)
    ):
        try:
            adapter.session_token = _decrypt_broker_session(cached["session_token"])
        except ValueError as exc:
            logger.warning("Cached Shoonya session could not be decrypted: %s", type(exc).__name__)
    return adapter


def _encrypt_broker_session(token: str) -> str:
    return encrypt_broker_session(token)


def _decrypt_broker_session(value: str) -> str:
    return decrypt_broker_session(value)


async def _cache_shoonya_session(request: Request, subject: str, adapter: ShoonyaAdapter) -> None:
    redis = request.app.state.redis
    if not redis.enabled or not adapter.session_token:
        return
    try:
        await redis.set_json(
            f"broker:session:shoonya:{subject}",
            {
                "user_id": adapter.user_id,
                "session_token": _encrypt_broker_session(adapter.session_token),
            },
            ttl_seconds=3600,
        )
    except (RedisError, ValueError) as exc:
        logger.warning("Could not cache Shoonya session: %s", type(exc).__name__)


async def _require_approved_broker_user(request: Request, subject: str) -> None:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Broker account status unavailable")
    try:
        user = await database.get_app_user(subject)
    except (SQLAlchemyError, RuntimeError, OSError, TimeoutError) as exc:
        logger.warning("Broker account status lookup failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Broker account status unavailable") from exc
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="An active user account is required")
    if user.kyc_status not in {"APPROVED", "VERIFIED_COMPLIANT"}:
        raise HTTPException(status_code=403, detail="KYC approval is required for broker login")


@router.post("/shoonya/login")
async def login_shoonya(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    await _require_approved_broker_user(request, subject)
    redis = request.app.state.redis
    if not redis.enabled:
        raise HTTPException(status_code=503, detail="Broker session storage unavailable")
    adapter = ShoonyaAdapter(redis=redis)
    try:
        data = await adapter.login()
        if not adapter.session_token:
            raise RuntimeError("Shoonya did not issue a session token")
        await redis.set_json(
            f"broker:session:shoonya:{subject}",
            {
                "user_id": adapter.user_id,
                "session_token": _encrypt_broker_session(adapter.session_token),
            },
            ttl_seconds=3600,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("Shoonya login transport failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Shoonya login request failed") from exc
    except (RedisError, ValueError) as exc:
        logger.warning("Shoonya session storage failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not store Shoonya session") from exc
    return {
        "success": True,
        "linked": True,
        "uname": str(data.get("uname") or adapter.user_id),
        "expiresInSeconds": 3600,
    }


@router.post("/coindcx/login")
async def login_coindcx(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    await _require_approved_broker_user(request, subject)
    try:
        result = await CoinDCXAdapter().login()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("CoinDCX login probe failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="CoinDCX authentication probe failed") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="CoinDCX returned an invalid authentication response") from exc
    return {
        "success": True,
        "linked": result["linked"],
        "positionsCount": result["positions_count"],
    }


@router.post("/test/shoonya/quote")
async def test_shoonya_quote(
    payload: ShoonyaQuoteRequest,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    cache_key = f"quote:shoonya:{payload.exchange}:{payload.symbol}"
    cached = await _cached_quote(request, cache_key)
    if cached:
        return {
            "success": True,
            "read_only": True,
            "quote": cached["quote"],
            "persistence": {**cached["persistence"], "redis": "cache_hit"},
            "persistence_degraded": any(
                value in {"error", "not_configured", "partial", "degraded", "offline"}
                for value in cached["persistence"].values()
                if isinstance(value, str)
            ) or bool(cached["persistence"].get("postgres_replication", {}).get("degraded")),
        }

    try:
        adapter = await _shoonya_adapter(request, subject)
        quote = await adapter.get_quote(payload.symbol, payload.exchange)
        await _cache_shoonya_session(request, subject, adapter)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RedisError as exc:
        logger.warning("Shoonya session cache unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Shoonya session cache unavailable") from exc
    except httpx.HTTPError as exc:
        logger.warning("Shoonya quote request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Shoonya quote request failed") from exc
    except (TypeError, ValueError) as exc:
        logger.warning("Shoonya quote response was invalid")
        raise HTTPException(status_code=502, detail="Shoonya returned an invalid quote response") from exc
    if quote is None:
        raise HTTPException(status_code=404, detail="Shoonya returned no quote for this exchange and instrument token")
    record = _quote_dict(quote, payload.symbol)
    return await _quote_response(request, subject, record, cache_key)


@router.post("/test/coindcx/quote")
async def test_coindcx_quote(
    payload: CoinDCXQuoteRequest,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    normalized = payload.symbol.upper()
    if not re.fullmatch(r"(?:[A-Z0-9]+USDT|B-[A-Z0-9]+_USDT)", normalized):
        raise HTTPException(status_code=422, detail="Use a CoinDCX futures pair such as BTCUSDT")
    cache_key = f"quote:coindcx:{normalized}"
    cached = await _cached_quote(request, cache_key)
    if cached:
        return {
            "success": True,
            "read_only": True,
            "quote": cached["quote"],
            "persistence": {**cached["persistence"], "redis": "cache_hit"},
            "persistence_degraded": any(
                value in {"error", "not_configured", "partial", "degraded", "offline"}
                for value in cached["persistence"].values()
                if isinstance(value, str)
            ) or bool(cached["persistence"].get("postgres_replication", {}).get("degraded")),
        }

    try:
        quote = await CoinDCXAdapter().get_quote(normalized)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("CoinDCX quote request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="CoinDCX quote request failed") from exc
    except (TypeError, ValueError) as exc:
        logger.warning("CoinDCX quote response was invalid")
        raise HTTPException(status_code=502, detail="CoinDCX returned an invalid quote response") from exc
    if quote is None:
        raise HTTPException(status_code=404, detail="CoinDCX returned no quote for this futures pair")
    record = _quote_dict(quote, normalized)
    return await _quote_response(request, subject, record, cache_key)


@router.get("/test/shoonya/positions")
async def test_shoonya_positions(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    try:
        adapter = await _shoonya_adapter(request, subject)
        rows = await adapter.get_positions()
        await _cache_shoonya_session(request, subject, adapter)
        positions = [_normalize_position(row, "SHOONYA") for row in rows]
        persistence = await _persist_positions(request, subject, "SHOONYA", rows)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("Shoonya positions request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Shoonya positions request failed") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Shoonya returned invalid positions data") from exc
    return {
        "success": True,
        "read_only": True,
        "positions": positions,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }


@router.get("/test/coindcx/positions")
async def test_coindcx_positions(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    try:
        rows = await CoinDCXAdapter().get_positions()
        positions = [_normalize_position(row, "COINDCX") for row in rows]
        persistence = await _persist_positions(request, subject, "COINDCX", rows)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("CoinDCX positions request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="CoinDCX positions request failed") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="CoinDCX returned invalid positions data") from exc
    return {
        "success": True,
        "read_only": True,
        "positions": positions,
        "persistence": persistence,
        "persistence_degraded": is_persistence_degraded(persistence),
    }

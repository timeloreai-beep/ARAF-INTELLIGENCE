from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from typing import Literal
from datetime import datetime, timezone

import aiohttp
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..brokers.coindcx import CoinDCXAdapter
from ..brokers.shoonya import ShoonyaAdapter
from ..dependencies import current_subject
from ..quant.engines import analyze_quant_features, evaluate_engine_suite
from ..schemas import OrderRequest
from ..services.apex_predator import (
    detect_retail_trap,
    evaluate_apex_setup,
    volume_profile_confirmation,
)
from ..services.automation import OpportunityHub, trading_day_ist
from ..services.ai_swarm import get_ai_swarm_service
from ..services.paper_lifecycle import (
    MANUAL_EXIT_COOLDOWN,
    close_paper_position_manually,
)
from ..services.options import INDEX_SPOT_INSTRUMENTS, select_liquid_shoonya_option
from ..services.scheduler import is_india_market_open, get_india_market_status
from ..services.scanner import UniverseScanner
from ..services.risk_persistence import load_risk_state, persist_risk_state
from .orders import _place_order, place_order

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/automation", tags=["automation"])


class AutomationSettingsPayload(BaseModel):
    crypto_enabled: bool | None = None
    india_enabled: bool | None = None
    daily_budget_inr: float | None = Field(default=None, ge=1000, le=100_000_000)
    allowed_markets: Literal["ALL", "INDIA", "CRYPTO"] | None = None


class AutomationScanPayload(BaseModel):
    market: Literal["INDIA", "CRYPTO"] = "CRYPTO"
    timeframe: Literal["1m", "5m", "15m", "1h"] = "15m"
    limit: int = Field(default=10, ge=1, le=30)


class AutoTradeExecutionLogEntry(BaseModel):
    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    score: int
    status: str
    is_auto_trade: bool = False


def _hub(request: Request) -> OpportunityHub:
    hub = getattr(request.app.state, "opportunity_hub", None)
    if hub is None:
        raise HTTPException(status_code=503, detail="Opportunity service is unavailable")
    return hub


def _crypto_base_signal(features: dict) -> tuple[str, float]:
    indicators = features["indicators"]
    trend = indicators.get("supertrend_10_3") or {}
    side = trend.get("direction")
    ema_fast = indicators.get("ema_9")
    ema_slow = indicators.get("ema_21")
    if side == "BUY" and ema_fast is not None and ema_slow is not None and ema_fast > ema_slow:
        return "BUY", 72
    if side == "SELL" and ema_fast is not None and ema_slow is not None and ema_fast < ema_slow:
        return "SELL", 72
    return "NEUTRAL", 0


async def _usd_inr_rate() -> float:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.exchangerate-api.com/v4/latest/USD",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as response:
            response.raise_for_status()
            data = await response.json()
    rate = float((data.get("rates") or {}).get("INR") or 0)
    date_text = data.get("date")
    if not date_text or not math.isfinite(rate) or rate <= 0:
        raise ValueError("FX feed returned invalid USD/INR data")
    updated = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - updated).total_seconds() > 36 * 3600:
        raise ValueError("FX feed is stale")
    return rate


async def _load_persisted_settings(request: Request, subject: str) -> dict:
    database = request.app.state.database
    if not database.enabled:
        return await _hub(request).get_settings(subject)
    try:
        record = await database.get_user_preferences(subject)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.exception("Could not load persisted automation settings")
        raise HTTPException(status_code=503, detail="Automation settings are unavailable") from exc
    stored = (record.preferences or {}).get("automation", {}) if record else {}
    settings = await _hub(request).get_settings(subject)
    settings.update(stored)
    await _hub(request).set_settings(subject, settings)
    return settings


@router.get("/settings")
async def get_automation_settings(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    settings = await _load_persisted_settings(request, subject)
    database = request.app.state.database
    control = None
    if database.enabled:
        try:
            control = await database.get_admin_control()
            budget = await database.get_auto_trade_budget(subject, trading_day_ist())
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="Global automation controls are unavailable") from exc
    else:
        budget = None
    return {
        "success": True,
        "automation": {
            **settings,
            "usedBudgetInr": float(budget.used_inr) if budget else 0,
            "reservedBudgetInr": float(budget.reserved_inr) if budget else 0,
            "globalEnabled": bool(control and control.auto_trade_enabled),
            "globalKillSwitch": bool(control and control.global_kill_switch),
            "paperLifecycleReady": bool(getattr(request.app.state, "paper_lifecycle_ready", False)),
            "liveTradingEnabled": False,
        },
    }


@router.put("/settings")
async def update_automation_settings(
    payload: AutomationSettingsPayload,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    settings = await _load_persisted_settings(request, subject)
    updates = payload.model_dump(exclude_none=True)
    # Check which markets are being enabled BEFORE popping
    enabling_crypto = updates.get("crypto_enabled") is True
    enabling_india = updates.get("india_enabled") is True
    if "daily_budget_inr" in updates:
        settings["dailyBudgetInr"] = updates.pop("daily_budget_inr")
    if "allowed_markets" in updates:
        settings["allowedMarkets"] = updates.pop("allowed_markets")
    if "crypto_enabled" in updates:
        settings["crypto_enabled"] = updates.pop("crypto_enabled")
    if "india_enabled" in updates:
        settings["india_enabled"] = updates.pop("india_enabled")
    settings.update(updates)

    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Persistent automation settings are unavailable")
    # Validate risk/kill-switch only when enabling a specific market
    if enabling_crypto or enabling_india:
        try:
            risk_state = await load_risk_state(
                request,
                subject,
                request.app.state.risk_guardian,
            )
            control = await database.get_admin_control()
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(
                status_code=503,
                detail="Risk and kill-switch state is unavailable",
            ) from exc
        if risk_state.is_hard_locked:
            raise HTTPException(
                status_code=403,
                detail={"code": "USER_RISK_HARD_LOCK_ACTIVE"},
            )
        if control and control.global_kill_switch:
            raise HTTPException(
                status_code=403,
                detail={"code": "GLOBAL_KILL_SWITCH_ACTIVE"},
            )
    try:
        record = await database.get_user_preferences(subject)
        preferences = dict(record.preferences or {}) if record else {}
        preferences["automation"] = settings
        result = await database.upsert_user_preferences(subject, preferences)
    except (RuntimeError, SQLAlchemyError) as exc:
        logger.exception("Could not persist automation settings")
        raise HTTPException(status_code=503, detail="Automation settings could not be persisted") from exc
    if not (result.primary_applied or result.backup_applied):
        raise HTTPException(status_code=503, detail="Automation settings could not be persisted")
    await _hub(request).set_settings(subject, settings)
    return {"success": True, "automation": settings}


@router.get("/opportunities")
async def get_opportunities(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    opportunities = await _hub(request).list_active(subject)
    return {"success": True, "opportunities": opportunities}


@router.get("/positions")
async def get_paper_positions(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Paper-position persistence is unavailable")
    try:
        positions = await database.list_user_paper_positions(subject)
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Paper positions are unavailable") from exc
    return {
        "success": True,
        "positions": [
            {
                "id": position.id,
                "clientOrderId": position.client_order_id,
                "symbol": position.symbol,
                "exchange": position.exchange,
                "side": position.side,
                "quantity": float(position.quantity),
                "entry": float(position.entry_price),
                "stopLoss": float(position.stop_loss),
                "takeProfit": float(position.take_profit),
                "trailingDistance": float(position.trailing_distance),
                "favorablePrice": float(position.favorable_price),
                "status": position.status,
                "isAutoTrade": bool(position.is_auto_trade),
                "exitPrice": float(position.exit_price) if position.exit_price is not None else None,
                "realizedPnl": float(position.realized_pnl),
                "openedAt": position.opened_at.isoformat(),
                "closedAt": position.closed_at.isoformat() if position.closed_at else None,
            }
            for position in positions
        ],
    }


@router.get("/execution-log")
async def get_execution_log(
    request: Request,
    subject: str = Depends(current_subject),
    limit: int = 100,
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Execution log persistence is unavailable")
    try:
        logs = await database.get_auto_trade_execution_log(subject, limit=limit)
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Execution log is unavailable") from exc
    return {
        "success": True,
        "logs": [
            {
                "id": log.id,
                "symbol": log.symbol,
                "side": log.side,
                "quantity": float(log.quantity),
                "entry_price": float(log.entry_price),
                "stop_loss": float(log.stop_loss),
                "take_profit": float(log.take_profit),
                "score": log.score,
                "status": log.status,
                "is_auto_trade": log.is_auto_trade,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ],
    }


@router.post("/execution-log")
async def add_execution_log(
    payload: AutoTradeExecutionLogEntry,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Execution log persistence is unavailable")
    try:
        status = await database.add_auto_trade_execution_log(
            user_id=subject,
            symbol=payload.symbol,
            side=payload.side,
            quantity=payload.quantity,
            entry_price=payload.entry_price,
            stop_loss=payload.stop_loss,
            take_profit=payload.take_profit,
            score=payload.score,
            status=payload.status,
            is_auto_trade=payload.is_auto_trade,
        )
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Execution log could not be saved") from exc
    return {"success": True, "persistence": status.as_dict()}


@router.post("/positions/{position_id}/exit")
async def manually_exit_paper_position(
    position_id: str,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Paper-position persistence is unavailable")
    try:
        initial_position = await database.get_user_paper_position(subject, position_id)
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Paper position is unavailable") from exc
    if initial_position is None:
        raise HTTPException(status_code=404, detail="Paper position was not found")
    locks = getattr(request.app.state, "paper_position_locks", None)
    if locks is None:
        locks = {}
        request.app.state.paper_position_locks = locks
    setup_locks = getattr(request.app.state, "paper_setup_locks", None)
    if setup_locks is None:
        setup_locks = {}
        request.app.state.paper_setup_locks = setup_locks
    lock = locks.setdefault(position_id, asyncio.Lock())
    setup = (
        subject,
        initial_position.symbol,
        initial_position.exchange,
        initial_position.side,
    )
    setup_lock = setup_locks.setdefault(setup, asyncio.Lock())

    async with lock, setup_lock:
        try:
            position = await database.get_user_paper_position(subject, position_id)
        except (RuntimeError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail="Paper position is unavailable") from exc
        if position is None:
            raise HTTPException(status_code=404, detail="Paper position was not found")
        if position.status != "OPEN":
            raise HTTPException(status_code=409, detail="Paper position is already closed")

        try:
            if position.exchange == "COINDCX":
                quote = await CoinDCXAdapter().get_quote(position.symbol)
                expected_source = "COINDCX"
            else:
                if not position.broker_token:
                    raise RuntimeError("Paper position has no Shoonya instrument token")
                quote = await ShoonyaAdapter(
                    redis=getattr(request.app.state, "redis", None)
                ).get_quote(position.broker_token, position.exchange)
                expected_source = "SHOONYA"
        except (RuntimeError, ValueError, OSError, httpx.HTTPError, RedisError) as exc:
            raise HTTPException(status_code=503, detail="A trusted exit quote is unavailable") from exc
        if quote is None or quote.source != expected_source:
            raise HTTPException(status_code=503, detail="A trusted exit quote is unavailable")

        current = {
            "id": position.id,
            "user_id": position.user_id,
            "client_order_id": position.client_order_id,
            "symbol": position.symbol,
            "exchange": position.exchange,
            "broker_token": position.broker_token,
            "side": position.side,
            "quantity": float(position.quantity),
            "entry_price": float(position.entry_price),
            "stop_loss": float(position.stop_loss),
            "take_profit": float(position.take_profit),
            "trailing_distance": float(position.trailing_distance),
            "favorable_price": float(position.favorable_price),
            "status": position.status,
            "is_auto_trade": bool(position.is_auto_trade),
            "exit_price": float(position.exit_price) if position.exit_price is not None else None,
            "realized_pnl": float(position.realized_pnl),
            "opened_at": position.opened_at,
            "closed_at": position.closed_at,
            "exit_reason": position.exit_reason,
            "manual_exit_blocked_until": position.manual_exit_blocked_until,
            "pnl_fx_to_inr": float(position.pnl_fx_to_inr or 1),
        }
        try:
            closed = close_paper_position_manually(
                current,
                float(quote.price),
                now=datetime.now(timezone.utc),
            )
            guardian = request.app.state.risk_guardian
            risk_state = await load_risk_state(request, subject, guardian)
            guardian.sync_pnl(
                subject,
                risk_state.daily_pnl_inr
                + float(closed["realized_pnl"])
                * float(position.pnl_fx_to_inr or 1),
            )
            risk_persistence = await persist_risk_state(
                request, subject, guardian.get_state(subject)
            )
            postgres = risk_persistence.get("postgres", {})
            risk_saved = risk_persistence.get("redis") == "stored" or any(
                isinstance(result, dict)
                and (result.get("primary_applied") or result.get("backup_applied"))
                for result in postgres.values()
            )
            if not risk_saved:
                raise RuntimeError("Manual exit P&L risk state could not be persisted")
            position_persistence = await database.upsert_paper_position(**closed)
        except (RuntimeError, ValueError, SQLAlchemyError) as exc:
            logger.exception("Could not persist manual paper-position exit")
            raise HTTPException(status_code=503, detail="Manual exit could not be persisted") from exc
        if not (
            position_persistence.primary_applied
            or position_persistence.backup_applied
        ):
            raise HTTPException(status_code=503, detail="Manual exit could not be persisted")

        await _hub(request).block_manual_exit_setup(
            subject,
            order_key=position.symbol,
            exchange=position.exchange,
            side=position.side,
            blocked_until=datetime.now(timezone.utc) + MANUAL_EXIT_COOLDOWN,
        )
        # Terminate AI Swarm for this closed order
        try:
            swarm = await get_ai_swarm_service(request.app)
            await swarm.terminate_swarm(position.client_order_id, closed["exit_reason"] or "MANUAL_EXIT")
            logger.info("AI Swarm terminated for manual exit order %s", position.client_order_id)
        except Exception as swarm_exc:
            logger.exception("Failed to terminate AI Swarm for manual exit: %s", swarm_exc)
        return {
            "success": True,
            "position_id": position.id,
            "status": "CLOSED",
            "exit_price": closed["exit_price"],
            "realized_pnl": closed["realized_pnl"],
            "exit_reason": closed["exit_reason"],
            "manual_exit_blocked_until": closed["manual_exit_blocked_until"].isoformat(),
            "risk": guardian.to_record(guardian.get_state(subject)),
        }


@router.post("/scan")
async def scan_for_opportunities(
    payload: AutomationScanPayload,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Persisted risk and budget controls are unavailable")
    try:
        risk_config = await database.get_risk_config(subject)
        daily_budget = await database.get_auto_trade_budget(subject, trading_day_ist())
        risk_state = await load_risk_state(
            request,
            subject,
            request.app.state.risk_guardian,
        )
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Risk and budget state is unavailable") from exc
    if not risk_config or risk_state.is_hard_locked:
        return {"success": True, "evaluated": 0, "opportunities": [], "blocked": {"RISK_CONFIG_UNAVAILABLE_OR_LOCKED": 1}}

    user_settings = await _load_persisted_settings(request, subject)
    try:
        control = await database.get_admin_control()
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Global automation controls are unavailable") from exc
    
    # Check if the specific market is enabled for this user
    if payload.market == "CRYPTO" and not user_settings.get("crypto_enabled"):
        return {
            "success": True,
            "evaluated": 0,
            "opportunities": [],
            "blocked": {"CRYPTO_AUTO_TRADE_DISABLED": 1},
        }
    if payload.market == "INDIA" and not user_settings.get("india_enabled"):
        return {
            "success": True,
            "evaluated": 0,
            "opportunities": [],
            "blocked": {"INDIA_AUTO_TRADE_DISABLED": 1},
        }
    
    news_engine = getattr(request.app.state, "news_engine", None)
    news_guard_active = bool(news_engine and news_engine.priority_guard_active)
    scanner = getattr(request.app.state, "universe_scanner", None) or UniverseScanner()

    market_status = get_india_market_status()
    if payload.market == "INDIA":
        if not market_status["is_open"]:
            return {
                "success": True,
                "evaluated": 0,
                "opportunities": [],
                "blocked": {"INDIA_MARKET_CLOSED": 1},
                "market_status": market_status,
            }

    try:
        usd_inr = await _usd_inr_rate() if payload.market == "CRYPTO" else 1.0
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        return {
            "success": True,
            "evaluated": 0,
            "opportunities": [],
            "blocked": {"USD_INR_FX_UNAVAILABLE": 1},
            "detail": str(exc),
            "market_status": market_status if payload.market == "INDIA" else None,
        }

    shoonya: ShoonyaAdapter | None = None
    if payload.market == "INDIA":
        shoonya = ShoonyaAdapter(redis=getattr(request.app.state, "redis", None))
        try:
            await shoonya.login()
        except (RuntimeError, httpx.HTTPError, ValueError, RedisError) as exc:
            logger.warning("Indian automation scan lacks an authenticated Shoonya feed: %s", type(exc).__name__)
            return {
                "success": True,
                "evaluated": 0,
                "opportunities": [],
                "blocked": {"SHOONYA_FEED_UNAVAILABLE": 1},
            }
        universe = await scanner.scan_india_tradingview(5000)
        index_candidates = [
            {
                "symbol": symbol,
                "exchange": spot_exchange,
                "name": symbol,
                "price": 0,
                "volume": 0,
                "eligible": True,
                "asset_class": "index",
            }
            for symbol, (_, spot_exchange) in INDEX_SPOT_INSTRUMENTS.items()
        ]
        candidates = index_candidates + sorted(
            (item for item in universe if item.get("eligible")),
            key=lambda item: float(item.get("volume") or 0),
            reverse=True,
        )[:payload.limit]
        universe.extend(index_candidates)
        category = "indian"
        exchange = None
    else:
        try:
            universe = await scanner.scan_crypto(1000)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail="Crypto market data sources are unavailable",
            ) from exc
        candidates = sorted(
            (item for item in universe if item.get("eligible")),
            key=lambda item: float(item.get("turnover") or 0),
            reverse=True,
        )[:payload.limit]
        category = "crypto"
        exchange = "COINDCX"

    news_snapshot = request.app.state.news_engine.snapshot()
    providers = news_snapshot.get("providers") or {}
    news_loaded = any(provider.get("status") == "healthy" for provider in providers.values())
    news_items = [
        {
            "title": item.get("title", ""),
            "trust_tier": item.get("trustTier", "MEDIA"),
            "impact": item.get("impact", "NEUTRAL"),
        }
        for item in news_snapshot.get("items", [])
        if item.get("title")
    ]

    semaphore = asyncio.Semaphore(5)
    blocked: dict[str, int] = {}
    reports = []
    published = []

    async def evaluate(item: dict) -> None:
        async with semaphore:
            try:
                instrument = None
                if payload.market == "INDIA":
                    if shoonya is None:
                        raise RuntimeError("Shoonya feed is unavailable")
                    if item.get("asset_class") == "index":
                        token, exchange = INDEX_SPOT_INSTRUMENTS[item["symbol"]]
                        instrument = {"token": token, "tsym": item["symbol"], "ls": 1}
                        trade_symbol = item["symbol"]
                    else:
                        instrument = await shoonya.search_instrument(item["symbol"], item["exchange"])
                        token = str(instrument["token"])
                        trade_symbol = str(instrument["tsym"])
                        exchange = item["exchange"]
                    candle_loader = lambda interval: shoonya.get_historical_candles(
                        exchange, token, interval=interval, limit=120
                    )
                else:
                    token = None
                    trade_symbol = item["symbol"]
                    exchange = "COINDCX"
                    candle_loader = lambda interval: scanner.get_crypto_closed_candles(
                        item["symbol"], interval=interval, limit=120
                    )

                required_timeframes = {"1m", "5m", "1h", payload.timeframe}
                candle_results = await asyncio.gather(
                    *(candle_loader(interval) for interval in required_timeframes)
                )
                candle_sets = dict(zip(required_timeframes, candle_results))
                if any(len(candle_sets[interval]) < 30 for interval in required_timeframes):
                    blocked["INSUFFICIENT_CLOSED_CANDLES"] = blocked.get("INSUFFICIENT_CLOSED_CANDLES", 0) + 1
                    return
                timeframe_sides = {
                    interval: _crypto_base_signal(analyze_quant_features(candle_sets[interval]))[0]
                    for interval in ("1m", "5m", "1h")
                }
                candles = candle_sets[payload.timeframe]
                features = analyze_quant_features(candles)
                side, base_score = _crypto_base_signal(features)
                quote = (
                    await shoonya.get_quote(token, exchange)
                    if payload.market == "INDIA" and shoonya
                    else await CoinDCXAdapter().get_quote(item["symbol"])
                )
                last = candles[-1]
                quote_price = float(quote.price) if quote else 0
                price_delta = abs(quote_price - last["close"]) / last["close"] if quote_price > 0 else float("inf")
                now_ms = int(time.time() * 1000)
                max_candle_age_ms = {"1m": 2, "5m": 6, "15m": 16, "1h": 61}[payload.timeframe] * 60 * 1000
                fresh = (
                    quote is not None
                    and quote.source == ("SHOONYA" if payload.market == "INDIA" else "COINDCX")
                    and price_delta <= 0.005
                    and last["close_time"] <= now_ms
                    and now_ms - last["close_time"] <= max_candle_age_ms
                )
                entry = quote_price if fresh else last["close"]
                atr_value = features["indicators"].get("atr_14")
                trap = detect_retail_trap(candles)
                candidate_side = trap["direction"] if trap else side
                if candidate_side == "BUY" and atr_value:
                    stop, target = entry - 1.5 * atr_value, entry + 3 * atr_value
                elif candidate_side == "SELL" and atr_value:
                    stop, target = entry + 1.5 * atr_value, entry - 3 * atr_value
                else:
                    stop, target = 0, 0
                bars = [{key: value for key, value in candle.items() if key != "close_time"} for candle in candles]
                volume_profile = volume_profile_confirmation(candles, candidate_side)
                smc = features.get("smc") or {}
                smc_confirmation = bool(trap) or any(
                    event.get("side") in {
                        "BULLISH" if candidate_side == "BUY" else "BEARISH",
                        candidate_side,
                    }
                    for name in ("fair_value_gaps", "order_blocks", "liquidity_sweeps")
                    for event in smc.get(name, [])
                )
                event_state = (
                    {"state": "CLEAR", "risk_level": "LOW", "count": 0}
                    if news_loaded and not news_guard_active
                    else {
                        "state": "HALT_NO_TRADE" if news_guard_active else "UNKNOWN",
                        "risk_level": "HIGH" if news_guard_active else "UNKNOWN",
                        "count": len(news_snapshot.get("priority_events", [])),
                    }
                )
                engine_universe = [
                    {
                        "category": category,
                        "order_key": row["symbol"],
                        "change_pct": float(row.get("change_pct") or 0),
                        "volume": float(row.get("volume") or 0),
                        "turnover": float(row.get("turnover") or row.get("volume") or 0),
                        "sector": row.get("sector"),
                    }
                    for row in universe
                    if row.get("symbol")
                ]
                report = evaluate_engine_suite({
                    "symbol": item["symbol"],
                    "order_key": trade_symbol,
                    "category": category,
                    "timeframe": payload.timeframe,
                    "ltp": entry,
                    "sl": stop,
                    "target": target,
                    "base_score": base_score,
                    "base_side": candidate_side,
                    "data_source": "SHOONYA_TPSERIES+SHOONYA_QUOTE" if payload.market == "INDIA" else "BINANCE_KLINES+COINDCX_QUOTE",
                    "data_quality": "LIVE" if fresh else "STALE",
                    "event_time_ms": now_ms,
                    "candles": bars,
                    "universe": engine_universe,
                    "macro": {"usd_inr": usd_inr},
                    "event_risk": event_state,
                    "news_fetch_state": "LOADED" if news_loaded else "UNLOADED",
                    "news_items": news_items,
                    "quant": {
                        "regime": {
                            "regime": "TRENDING_BULL" if candidate_side == "BUY" else "TRENDING_BEAR" if candidate_side == "SELL" else "UNKNOWN",
                            "strategy_allowlist": ["trend"] if candidate_side != "NEUTRAL" else [],
                        },
                        "correlation": {"status": "UNAVAILABLE", "samples": 0},
                        "walk_forward": {"status": "INSUFFICIENT_DATA", "trades": 0},
                    },
                    "positions": [],
                    "execution": {
                        "broker": "SHOONYA" if payload.market == "INDIA" else "COINDCX",
                        "segment": "EQ" if payload.market == "INDIA" else "FUTURES",
                        "lot_size": float((instrument or {}).get("ls") or 1) if payload.market == "INDIA" else 0.001,
                    },
                    "sizing": {
                        "daily_budget_inr": user_settings["dailyBudgetInr"],
                        "used_budget_inr": float(daily_budget.used_inr) if daily_budget else 0,
                        "reserved_budget_inr": float(daily_budget.reserved_inr) if daily_budget else 0,
                        "capital_inr": float(risk_config.capital_inr),
                        "usd_inr": usd_inr,
                    },
                    "session": {"is_open": True, "state": "CRYPTO_24_7"} if payload.market == "CRYPTO" else market_status,
                    "scorecard": {"total_recorded": 0, "reject_rate_pct": 0},
                })
                report["execution"] = {
                    "broker": "SHOONYA" if payload.market == "INDIA" else "COINDCX",
                    "segment": "EQ" if payload.market == "INDIA" else "FUTURES",
                    "lot_size": float((instrument or {}).get("ls") or 1)
                    if payload.market == "INDIA"
                    else 0.001,
                }
                apex = evaluate_apex_setup(
                    score=int(report["fusionScore"]),
                    side=candidate_side,
                    timeframe_sides=timeframe_sides,
                    required_gates={
                        "smc": smc_confirmation,
                        "volume": bool(volume_profile["passed"]),
                        "news": news_loaded and not news_guard_active,
                        "closed_candles": all(candle_sets[interval][-1]["is_closed"] for interval in required_timeframes),
                    },
                    trap=trap,
                )
                report["apexSetup"] = apex
                report["volumeProfile"] = volume_profile
                report["side"] = apex["side"] if apex["allowed"] else candidate_side
                report["orderKey"] = trade_symbol
                report["exchange"] = exchange
                report["brokerToken"] = token
                execution_atr = float(atr_value) if atr_value else None
                execution_lot_size = (
                    float((instrument or {}).get("ls") or 1)
                    if payload.market == "INDIA"
                    else 0.001
                )
                if payload.market == "INDIA" and apex["allowed"] and report["allRequiredPass"]:
                    if shoonya is None:
                        raise RuntimeError("Shoonya feed is unavailable")
                    signal_side = apex["side"]
                    try:
                        option = await select_liquid_shoonya_option(
                            shoonya,
                            underlying=item["symbol"],
                            direction="BULLISH" if signal_side == "BUY" else "BEARISH",
                        )
                    except RuntimeError as exc:
                        no_stock_options = (
                            item.get("asset_class") != "index"
                            and str(exc) in {
                                "No active Shoonya option expiry is available for this underlying",
                                "No ATM/ITM option contracts are available for the active expiry",
                            }
                        )
                        if not no_stock_options:
                            raise
                        report["bengaliSummary"] += (
                            " তালিকাভুক্ত option contract না থাকায় liquid underlying equity-তে "
                            "paper setup গণনা করা হয়েছে।"
                        )
                    else:
                        option_candles = await shoonya.get_historical_candles(
                            option["exchange"],
                            option["token"],
                            interval=payload.timeframe,
                            limit=120,
                        )
                        if len(option_candles) < 30:
                            raise RuntimeError("Shoonya option candles are insufficient")
                        option_features = analyze_quant_features(option_candles)
                        execution_atr = option_features["indicators"].get("atr_14")
                        if not execution_atr or not math.isfinite(float(execution_atr)):
                            raise RuntimeError("Shoonya option ATR is unavailable")
                        option_entry = float(option["entry_price"])
                        report.update({
                            "symbol": f"{item['symbol']} {option['expiry']} {option['strike']} {option['option_type']}",
                            "underlying": item["symbol"],
                            "signalSide": signal_side,
                            "optionType": option["option_type"],
                            "side": "BUY",
                            "orderKey": option["trading_symbol"],
                            "exchange": option["exchange"],
                            "brokerToken": option["token"],
                            "entry": option_entry,
                            "sl": option_entry - 1.5 * float(execution_atr),
                            "target": option_entry + 3 * float(execution_atr),
                            "optionContract": option,
                            "bengaliSummary": (
                                f"{report['bengaliSummary']} "
                                f"{'বুলিশ' if signal_side == 'BUY' else 'বেয়ারিশ'} confluence-এ "
                                f"{option['option_type']} বাছাই হয়েছে; bid-ask spread "
                                f"{option['spread_pct']:.2f}%।"
                            ),
                        })
                        report["execution"]["segment"] = "OPTIONS"
                        report["execution"]["lot_size"] = option["lot_size"]
                        execution_lot_size = float(option["lot_size"])
                if trap:
                    report["bengaliSummary"] += f" {trap['explanation_bn']}"
                if await database.has_manual_exit_block(
                    subject,
                    report["orderKey"],
                    report["exchange"],
                    report["side"],
                ):
                    blocked["MANUAL_EXIT_REENTRY_LOCK"] = (
                        blocked.get("MANUAL_EXIT_REENTRY_LOCK", 0) + 1
                    )
                    return
                reports.append({
                    "symbol": item["symbol"],
                    "fusionScore": report["fusionScore"],
                    "autoTradeEligible": report["autoTradeEligible"],
                    "apexEligible": apex["allowed"],
                    "allRequiredPass": report["allRequiredPass"],
                    "blockReasons": report["blockReasons"],
                })
                opportunity = await _hub(request).publish(
                    subject=subject,
                    report=report,
                    trusted_feed=fresh,
                    scanner_gate_passed=bool(item["eligible"]),
                    news_guard_active=news_guard_active,
                    global_auto_trade_enabled=bool(control and control.auto_trade_enabled),
                    global_kill_switch=bool(control and control.global_kill_switch),
                    lifecycle_ready=bool(getattr(request.app.state, "paper_lifecycle_ready", False)),
                    capital_inr=float(risk_config.capital_inr),
                    risk_pct=float(risk_config.max_per_trade_risk_pct),
                    atr_value=execution_atr,
                    used_budget_inr=(
                        float(daily_budget.used_inr) + float(daily_budget.reserved_inr)
                        if daily_budget
                        else 0
                    ),
                    fx_to_inr=usd_inr,
                    lot_size=execution_lot_size,
                )
                if opportunity:
                    if opportunity["autoTradeEligible"]:
                        auto_order = OrderRequest(
                            symbol=opportunity["orderKey"],
                            exchange=opportunity["exchange"],
                            side=opportunity["side"],
                            quantity=opportunity["quantity"],
                            operation="ENTRY",
                            mode="PAPER",
                            entry_price=opportunity["entry"],
                            stop_loss=opportunity["sl"],
                            take_profit=opportunity["target"],
                            broker_token=opportunity.get("brokerToken"),
                            trailing_distance=opportunity.get("trailingDistance"),
                            client_order_id=f"AUTO_{opportunity['id'].replace('-', '')[:28]}",
                            is_auto_trade=True,
                            automation_opportunity_id=opportunity["id"],
                            macro_green=True,
                            smc_green=True,
                            mtf_green=True,
                            volume_green=True,
                        )
                        try:
                            placed = await place_order(
                                auto_order,
                                request,
                                subject,
                                request.app.state.risk_guardian,
                            )
                            auto_state = str(placed.get("state") or "PAPER_FILLED")
                            # Save auto-trade execution log
                            try:
                                await database.add_auto_trade_execution_log(
                                    user_id=subject,
                                    symbol=opportunity["orderKey"],
                                    side=opportunity["side"],
                                    quantity=opportunity["quantity"],
                                    entry_price=opportunity["entry"],
                                    stop_loss=opportunity["sl"],
                                    take_profit=opportunity["target"],
                                    score=int(opportunity.get("fusionScore", 0)),
                                    status=auto_state,
                                    is_auto_trade=True,
                                )
                            except (RuntimeError, SQLAlchemyError) as exc:
                                logger.warning("Failed to save auto-trade execution log: %s", type(exc).__name__)

                            # Activate 100-Agent AI Swarm for this open order
                            try:
                                swarm = await get_ai_swarm_service(request.app)
                                await swarm.start_swarm_for_order(
                                    order_id=placed.get("client_order_id", auto_order.client_order_id),
                                    symbol=opportunity["orderKey"],
                                    side=opportunity["side"],
                                    entry_price=opportunity["entry"],
                                    stop_loss=opportunity["sl"],
                                    take_profit=opportunity["target"],
                                    trailing_sl=opportunity.get("trailingDistance", 0) or abs(opportunity["entry"] - opportunity["sl"]),
                                    quantity=opportunity["quantity"],
                                    exchange=opportunity["exchange"],
                                    broker_token=opportunity.get("brokerToken", ""),
                                    is_auto_trade=True,
                                )
                                logger.info("AI Swarm activated for auto-trade order %s", placed.get("client_order_id"))
                            except Exception as swarm_exc:
                                logger.exception("Failed to activate AI Swarm for auto-trade: %s", swarm_exc)
                        except HTTPException as exc:
                            auto_state = f"BLOCKED: {exc.detail}"
                            logger.warning(
                                "Server auto-trade was blocked for %s: %s",
                                item["symbol"],
                                exc.detail,
                            )
                        if auto_state.startswith("BLOCKED:"):
                            manual_alert = await _hub(request).restore_manual_alert(
                                subject,
                                opportunity,
                                auto_trade_state=auto_state,
                            )
                            if manual_alert:
                                published.append(manual_alert)
                    else:
                        published.append(opportunity)
                elif not report["allRequiredPass"]:
                    blocked["REQUIRED_ENGINE_GATE"] = blocked.get("REQUIRED_ENGINE_GATE", 0) + 1
                elif not apex["allowed"]:
                    blocked["APEX_CONFLUENCE_GATE"] = blocked.get("APEX_CONFLUENCE_GATE", 0) + 1
            except (ValueError, RuntimeError, OSError, aiohttp.ClientError, httpx.HTTPError, TypeError) as exc:
                logger.warning("Server-side opportunity scan failed for %s: %s", item.get("symbol"), type(exc).__name__)
                blocked["TRUSTED_DATA_UNAVAILABLE"] = blocked.get("TRUSTED_DATA_UNAVAILABLE", 0) + 1

    await asyncio.gather(*(evaluate(item) for item in candidates))
    return {
        "success": True,
        "evaluated": len(reports),
        "reports": reports,
        "opportunities": published,
        "blocked": blocked,
        "market": payload.market,
        "executionMode": "PAPER",
        "liveTradingEnabled": False,
        "market_status": market_status,
    }


@router.post("/opportunities/{opportunity_id}/claim")
async def claim_opportunity(
    opportunity_id: str,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Trade controls are unavailable")
    try:
        control = await database.get_admin_control()
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Trade controls are unavailable") from exc
    if control and control.global_kill_switch:
        raise HTTPException(status_code=403, detail={"code": "GLOBAL_KILL_SWITCH_ACTIVE"})

    active = await _hub(request).list_active(subject)
    candidate = next((item for item in active if item["id"] == opportunity_id), None)
    if candidate and (
        not candidate.get("executionEligible")
        or int(candidate.get("fusionScore", 0)) < 70
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "CONFLUENCE_BELOW_EXECUTION_THRESHOLD", "minimum_score": 70},
        )
    opportunity = await _hub(request).claim(subject, opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=410, detail={"code": "OPPORTUNITY_EXPIRED_OR_CLAIMED"})
    exchange = opportunity.get("exchange")
    if exchange not in {"NSE", "BSE", "NFO", "BFO", "COINDCX"}:
        raise HTTPException(status_code=403, detail="Opportunity has no supported execution route")

    order = OrderRequest(
        symbol=opportunity["orderKey"],
        exchange=exchange,
        side=opportunity["side"],
        quantity=opportunity["quantity"],
        operation="ENTRY",
        mode="PAPER",
        entry_price=opportunity["entry"],
        stop_loss=opportunity["sl"],
        take_profit=opportunity["target"],
        broker_token=opportunity.get("brokerToken"),
        trailing_distance=opportunity.get("trailingDistance"),
        client_order_id=f"OPP_{uuid.uuid4().hex[:24]}",
        is_auto_trade=False,
    )
    result = await _place_order(
        order,
        request,
        subject,
        request.app.state.risk_guardian,
        trusted_opportunity=opportunity,
    )
    # Activate 100-Agent AI Swarm for this manual open order
    try:
        swarm = await get_ai_swarm_service(request.app)
        await swarm.start_swarm_for_order(
            order_id=result.get("client_order_id", order.client_order_id),
            symbol=opportunity["orderKey"],
            side=opportunity["side"],
            entry_price=opportunity["entry"],
            stop_loss=opportunity["sl"],
            take_profit=opportunity["target"],
            trailing_sl=opportunity.get("trailingDistance", 0) or abs(opportunity["entry"] - opportunity["sl"]),
            quantity=opportunity["quantity"],
            exchange=opportunity["exchange"],
            broker_token=opportunity.get("brokerToken", ""),
            is_auto_trade=False,
        )
        logger.info("AI Swarm activated for manual order %s", result.get("client_order_id"))
    except Exception as swarm_exc:
        logger.exception("Failed to activate AI Swarm for manual order: %s", swarm_exc)
    return {"success": True, "opportunity_id": opportunity_id, "order": result}

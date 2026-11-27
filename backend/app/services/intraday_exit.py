from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from ..brokers.shoonya import ShoonyaAdapter
from ..integrations.database import DatabaseHub
from ..integrations.redis import RedisCache
from ..services.ai_swarm import get_ai_swarm_service
from ..quant.engines import evaluate_engine_suite, ENGINE_METADATA
from ..services.scheduler import INDIA_TIMEZONE, is_india_market_open, is_india_market_holiday
from ..config import get_settings

logger = logging.getLogger(__name__)

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"}
INTRADAY_EXIT_TIME = time(15, 20)
INDEX_CONFIDENCE_THRESHOLD = 90
EXPIRY_BUFFER_DAYS = 1

class IntradayExitEngine:
    def __init__(
        self,
        database: DatabaseHub,
        redis: RedisCache,
        shoonya: ShoonyaAdapter,
    ) -> None:
        self.database = database
        self.redis = redis
        self.shoonya = shoonya
        self._last_run_date: date | None = None

    def _is_index_symbol(self, symbol: str) -> bool:
        base = symbol.split("-")[0].upper()
        return base in INDEX_SYMBOLS

    def _is_intraday_product(self, position: Any) -> bool:
        return not getattr(position, "is_auto_trade", False) or getattr(position, "is_auto_trade", False)

    async def _get_live_price(self, symbol: str, exchange: str, broker_token: str | None) -> float | None:
        try:
            if exchange == "COINDCX":
                from ..brokers.coindcx import CoinDCXAdapter
                quote = await CoinDCXAdapter().get_quote(symbol)
                if quote and quote.price > 0:
                    return quote.price
            else:
                if not broker_token:
                    return None
                quote = await self.shoonya.get_quote(broker_token, exchange)
                if quote and quote.price > 0:
                    return quote.price
        except Exception as exc:
            logger.warning("Failed to get live price for %s/%s: %s", exchange, symbol, exc)
        return None

    async def _evaluate_index_next_day_confidence(
        self,
        symbol: str,
        exchange: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        try:
            from ..services.price_feeds import get_price_feed_manager
            from ..services.scanner import UniverseScanner
            
            price_manager = get_price_feed_manager()
            scanner = UniverseScanner()
            
            candles = await price_manager.get_candles(symbol, exchange, "15m", 120)
            if not candles or len(candles) < 20:
                return 0
            
            closed_bars = [bar for bar in candles if bar.get("is_closed")]
            if len(closed_bars) < 16:
                return 0
            
            universe = await scanner.scan_universe("INDIA", 100)
            
            macro = await self._get_macro_data()
            
            payload = {
                "symbol": symbol,
                "order_key": symbol,
                "category": "indian",
                "base_side": side,
                "ltp": entry_price,
                "sl": stop_loss,
                "target": take_profit,
                "candles": closed_bars,
                "universe": universe,
                "news_items": [],
                "macro": macro,
                "data_source": "SHOONYA",
                "data_quality": "LIVE",
                "event_time_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
            
            result = evaluate_engine_suite(payload)
            fusion_score = result.get("fusionScore", 0)
            return min(100, max(0, fusion_score))
        except Exception as exc:
            logger.warning("Failed to evaluate index confidence for %s: %s", symbol, exc)
            return 0

    async def _get_macro_data(self) -> dict[str, Any]:
        try:
            from ..services.price_feeds import get_price_feed_manager
            price_manager = get_price_feed_manager()
            usd_inr = await price_manager.get_quote("USDINR", "FX")
            vix = await price_manager.get_quote("INDIAVIX", "INDEX")
            gold = await price_manager.get_quote("GOLD", "COMMODITY")
            return {
                "usd_inr": usd_inr.price if usd_inr else None,
                "vix": vix.price if vix else None,
                "gold_change_pct": gold.price if gold else None,
            }
        except Exception:
            return {}

    def _has_expiry_remaining(self, position: Any, now_ist: datetime) -> bool:
        expiry_str = getattr(position, "expiry", None) or (position.payload or {}).get("expiry") if hasattr(position, "payload") else None
        if not expiry_str:
            return False
        try:
            expiry_date = date.fromisoformat(expiry_str)
            days_left = (expiry_date - now_ist.date()).days
            return days_left > EXPIRY_BUFFER_DAYS
        except Exception:
            return False

    def _is_carry_forward_eligible(self, position: Any) -> bool:
        product = (position.payload or {}).get("product") if hasattr(position, "payload") else None
        return product in {"NRML", "CNC", "MIS"} if product else True

    def _tp_hit(self, position: Any, live_price: float) -> bool:
        side = str(position.side).upper()
        tp = float(position.take_profit)
        if side == "BUY":
            return live_price >= tp
        return live_price <= tp

    async def _exit_paper_position(
        self,
        position: Any,
        live_price: float,
        reason: str,
        now: datetime,
    ) -> bool:
        try:
            from ..services.paper_lifecycle import close_paper_position_manually
            updated = close_paper_position_manually(
                {
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
                    "is_auto_trade": position.is_auto_trade,
                    "exit_price": position.exit_price,
                    "realized_pnl": float(position.realized_pnl),
                    "opened_at": position.opened_at,
                    "closed_at": position.closed_at,
                    "exit_reason": position.exit_reason,
                    "manual_exit_blocked_until": position.manual_exit_blocked_until,
                    "pnl_fx_to_inr": float(position.pnl_fx_to_inr or 1),
                },
                live_price,
                now=now,
            )
            status = await self.database.upsert_paper_position(**updated)
            # Terminate AI Swarm for this closed order
            try:
                swarm = await get_ai_swarm_service(SimpleNamespace(state=SimpleNamespace(database=self.database)))
                await swarm.terminate_swarm(position.client_order_id, reason)
                logger.info("AI Swarm terminated for intraday exit order %s", position.client_order_id)
            except Exception as swarm_exc:
                logger.exception("Failed to terminate AI Swarm for intraday exit: %s", swarm_exc)
            return status.primary_applied or status.backup_applied
        except Exception as exc:
            logger.exception("Failed to exit paper position %s: %s", position.id, exc)
            return False

    async def _exit_live_position(
        self,
        position: Any,
        live_price: float,
        reason: str,
        now: datetime,
    ) -> bool:
        try:
            from ..schemas import OrderRequest
            from ..services.automation import trading_day_ist
            from ..routers.orders import _place_order_unlocked
            from types import SimpleNamespace
            
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                database=self.database,
                redis=self.redis,
                mongo=SimpleNamespace(enabled=False),
            )))
            
            exit_order = OrderRequest(
                client_order_id=f"EXIT_{position.client_order_id}_{int(now.timestamp())}",
                symbol=position.symbol,
                exchange=position.exchange,
                side="SELL" if position.side == "BUY" else "BUY",
                operation="EXIT",
                mode="PAPER" if position.mode == "PAPER" else "LIVE",
                quantity=position.quantity,
                entry_price=live_price,
                stop_loss=live_price,
                take_profit=live_price,
                broker_token=position.broker_token,
                is_auto_trade=position.is_auto_trade,
            )
            
            result = await _place_order_unlocked(exit_order, request, position.user_id, None)
            return result.get("success", False)
        except Exception as exc:
            logger.exception("Failed to exit live position %s: %s", position.client_order_id, exc)
            return False

    async def run_intraday_exit(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        now_ist = current.astimezone(INDIA_TIMEZONE)
        
        if now_ist.time() < INTRADAY_EXIT_TIME:
            return {"status": "skipped", "reason": "before_exit_time", "ist_time": now_ist.isoformat()}
        
        if self._last_run_date == now_ist.date():
            return {"status": "skipped", "reason": "already_ran_today", "date": str(now_ist.date())}
        
        if now_ist.weekday() >= 5 or is_india_market_holiday(now_ist.date()):
            return {"status": "skipped", "reason": "market_closed", "date": str(now_ist.date())}
        
        self._last_run_date = now_ist.date()
        
        logger.info("Starting 15:20 IST Intraday Auto-Exit at %s IST", now_ist.strftime("%H:%M:%S"))
        
        try:
            positions = await self.database.list_open_paper_positions()
        except Exception as exc:
            logger.exception("Failed to fetch open paper positions")
            return {"status": "error", "reason": "position_fetch_failed", "error": str(exc)}
        
        india_positions = [
            p for p in positions
            if p.exchange in {"NSE", "BSE", "NFO", "BFO"} and p.status == "OPEN"
        ]
        
        if not india_positions:
            logger.info("No open Indian equity positions to process at 15:20 IST")
            return {"status": "completed", "processed": 0, "exited": 0, "held": 0, "ist_time": now_ist.isoformat()}
        
        results = {"exited": 0, "held": 0, "errors": 0, "details": []}
        
        for position in india_positions:
            try:
                live_price = await self._get_live_price(
                    position.symbol, position.exchange, position.broker_token
                )
                
                if live_price is None or live_price <= 0:
                    logger.warning("No live price for %s/%s, skipping", position.exchange, position.symbol)
                    results["errors"] += 1
                    results["details"].append({
                        "symbol": position.symbol,
                        "exchange": position.exchange,
                        "action": "error",
                        "reason": "no_live_price",
                    })
                    continue
                
                is_index = self._is_index_symbol(position.symbol)
                tp_hit = self._tp_hit(position, live_price)
                has_expiry = self._has_expiry_remaining(position, now_ist)
                carry_eligible = self._is_carry_forward_eligible(position)
                
                if is_index and has_expiry and not tp_hit and carry_eligible:
                    confidence = await self._evaluate_index_next_day_confidence(
                        position.symbol,
                        position.exchange,
                        position.side,
                        float(position.entry_price),
                        float(position.stop_loss),
                        float(position.take_profit),
                    )
                    
                    if confidence >= INDEX_CONFIDENCE_THRESHOLD:
                        logger.info(
                            "INDEX HOLD: %s %s confidence=%d%% >= %d%%, skipping 15:20 exit",
                            position.symbol, position.side, confidence, INDEX_CONFIDENCE_THRESHOLD
                        )
                        results["held"] += 1
                        results["details"].append({
                            "symbol": position.symbol,
                            "exchange": position.exchange,
                            "action": "held_overnight",
                            "reason": f"index_confidence_{confidence}%",
                            "confidence": confidence,
                            "live_price": live_price,
                        })
                        continue
                    else:
                        logger.info(
                            "INDEX EXIT: %s %s confidence=%d%% < %d%%, exiting at 15:20",
                            position.symbol, position.side, confidence, INDEX_CONFIDENCE_THRESHOLD
                        )
                
                if position.is_auto_trade:
                    exited = await self._exit_paper_position(position, live_price, "INTRADAY_AUTO_EXIT_1520", now_ist)
                else:
                    exited = await self._exit_paper_position(position, live_price, "INTRADAY_MANUAL_EXIT_1520", now_ist)
                
                if exited:
                    results["exited"] += 1
                    results["details"].append({
                        "symbol": position.symbol,
                        "exchange": position.exchange,
                        "action": "exited",
                        "reason": "intraday_1520_exit",
                        "live_price": live_price,
                        "is_auto_trade": position.is_auto_trade,
                    })
                    logger.info(
                        "EXITED %s %s %s@%.2f (%s) at 15:20 IST",
                        "AUTO" if position.is_auto_trade else "MANUAL",
                        position.side, position.symbol, live_price, position.exchange
                    )
                else:
                    results["errors"] += 1
                    results["details"].append({
                        "symbol": position.symbol,
                        "exchange": position.exchange,
                        "action": "error",
                        "reason": "exit_failed",
                    })
                    
            except Exception as exc:
                logger.exception("Error processing position %s: %s", position.id, exc)
                results["errors"] += 1
                results["details"].append({
                    "symbol": position.symbol,
                    "exchange": position.exchange,
                    "action": "error",
                    "reason": str(exc),
                })
        
        logger.info(
            "15:20 Intraday Exit completed: exited=%d, held=%d, errors=%d",
            results["exited"], results["held"], results["errors"]
        )
        
        return {
            "status": "completed",
            "processed": len(india_positions),
            **results,
            "ist_time": now_ist.isoformat(),
        }


async def _intraday_exit_worker(app: Any) -> None:
    database = app.state.database
    redis = app.state.redis
    shoonya = ShoonyaAdapter(redis=redis)
    
    engine = IntradayExitEngine(database, redis, shoonya)
    
    logger.info("Intraday Auto-Exit worker started - will run at 15:20 IST on market days")
    
    while True:
        try:
            now = datetime.now(timezone.utc)
            now_ist = now.astimezone(INDIA_TIMEZONE)
            
            target_today = datetime.combine(now_ist.date(), INTRADAY_EXIT_TIME, tzinfo=INDIA_TIMEZONE)
            
            if now_ist >= target_today:
                target_today += timedelta(days=1)
            
            while target_today.weekday() >= 5 or is_india_market_holiday(target_today.date()):
                target_today += timedelta(days=1)
            
            wait_seconds = (target_today - now_ist).total_seconds()
            
            logger.info("Next 15:20 IST intraday exit scheduled in %.0f seconds (%s IST)", wait_seconds, target_today.strftime("%Y-%m-%d %H:%M:%S"))
            
            await asyncio.sleep(wait_seconds)
            
            if not get_settings().app_env.lower() == "production":
                logger.info("Skipping 15:20 exit - not in production environment")
                continue
            
            result = await engine.run_intraday_exit()
            logger.info("15:20 Intraday Exit result: %s", result)
            
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Intraday exit worker error: %s", exc)
            await asyncio.sleep(60)
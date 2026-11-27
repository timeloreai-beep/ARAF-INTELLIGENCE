from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass, field
from collections import defaultdict

from ..quant.engines import evaluate_engine_suite, analyze_quant_features
from ..services.price_feeds import get_price_feed_manager
from ..services.news import NewsEngine
from ..integrations.database import DatabaseHub
from ..integrations.redis import RedisCache

logger = logging.getLogger(__name__)

AGENT_COUNT = 100
CONSENSUS_THRESHOLD = 1.0
SWARM_TICK_INTERVAL = 0.5


@dataclass
class AgentVerdict:
    agent_id: int
    engine_id: str
    category: str
    verdict: str
    confidence: float
    evidence: str
    timestamp_ms: int


@dataclass
class SwarmState:
    order_id: str
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    trailing_sl: float
    quantity: float
    exchange: str
    broker_token: str
    is_auto_trade: bool
    created_at: datetime
    agents_active: int = AGENT_COUNT
    last_consensus_score: float = 0.0
    last_verdict: str = "HOLD"
    last_tick_ms: int = 0
    is_terminated: bool = False


class AISwarmAgent:
    def __init__(self, agent_id: int, engine_focus: str):
        self.agent_id = agent_id
        self.engine_focus = engine_focus

    async def evaluate(
        self,
        swarm_state: SwarmState,
        market_data: dict[str, Any],
        engine_results: list[dict[str, Any]],
        news_items: list[dict],
        macro_data: dict,
        quant_features: dict,
    ) -> AgentVerdict:
        await asyncio.sleep(0.001)

        relevant_engines = [e for e in engine_results if e["engineId"] == self.engine_focus]
        if not relevant_engines:
            return AgentVerdict(
                agent_id=self.agent_id,
                engine_id=self.engine_focus,
                category="unknown",
                verdict="HOLD",
                confidence=0.0,
                evidence="No engine data",
                timestamp_ms=int(time.time() * 1000),
            )

        engine = relevant_engines[0]
        status = engine["status"]
        direction = engine["direction"]
        evidence = engine["evidence"]
        score_delta = engine["scoreDelta"]
        engine_id = engine["engineId"]

        # SHIKARI Hunter Unwind = IMMEDIATE EXIT OVERRIDE (bypasses consensus)
        if engine_id == "ENG25_SHIKARI_HUNTER_UNWIND" and status == "FAIL":
            return AgentVerdict(
                agent_id=self.agent_id,
                engine_id=engine_id,
                category=engine.get("label", "Shikari Hunter Unwind"),
                verdict="EXIT_HUNTER_UNWIND",
                confidence=1.0,
                evidence=f"SHIKARI HUNTER UNWIND: {evidence}",
                timestamp_ms=int(time.time() * 1000),
            )

        # SHIKARI Dump Shield = VETO entry / EXIT long immediately
        if engine_id == "ENG23_SHIKARI_DUMP_SHIELD" and status in {"FAIL", "WARN"} and direction == "SELL":
            if swarm_state.side == "BUY":
                return AgentVerdict(
                    agent_id=self.agent_id,
                    engine_id=engine_id,
                    category=engine.get("label", "Shikari Dump Shield"),
                    verdict="EXIT_DUMP_SHIELD",
                    confidence=0.98,
                    evidence=f"SHIKARI DUMP SHIELD: {evidence}",
                    timestamp_ms=int(time.time() * 1000),
                )
            return AgentVerdict(
                agent_id=self.agent_id,
                engine_id=engine_id,
                category=engine.get("label", "Shikari Dump Shield"),
                verdict="VETO_ENTRY",
                confidence=0.95,
                evidence=f"SHIKARI DUMP SHIELD: {evidence}",
                timestamp_ms=int(time.time() * 1000),
            )

        # SHIKARI Sweep Reclaim = Enter WITH Hunter (confirmation)
        if engine_id == "ENG21_SHIKARI_SWEEP_RECLAIM" and status == "PASS" and direction == swarm_state.side:
            return AgentVerdict(
                agent_id=self.agent_id,
                engine_id=engine_id,
                category=engine.get("label", "Shikari Sweep Reclaim"),
                verdict="HOLD_CONFIRMED",
                confidence=0.90,
                evidence=f"SHIKARI SWEEP RECLAIM: {evidence}",
                timestamp_ms=int(time.time() * 1000),
            )

        # SHIKARI Whale Absorption = Enter WITH Whale (confirmation)
        if engine_id == "ENG22_SHIKARI_WHALE_ABSORPTION" and status == "PASS" and direction == swarm_state.side:
            return AgentVerdict(
                agent_id=self.agent_id,
                engine_id=engine_id,
                category=engine.get("label", "Shikari Whale Absorption"),
                verdict="HOLD_CONFIRMED",
                confidence=0.88,
                evidence=f"SHIKARI WHALE ABSORPTION: {evidence}",
                timestamp_ms=int(time.time() * 1000),
            )

        # SHIKARI FII/DII OI Alignment = Enter WITH Institutions (confirmation)
        if engine_id == "ENG24_SHIKARI_FII_DII_OI" and status == "PASS" and direction == swarm_state.side:
            return AgentVerdict(
                agent_id=self.agent_id,
                engine_id=engine_id,
                category=engine.get("label", "Shikari FII/DII OI"),
                verdict="HOLD_CONFIRMED",
                confidence=0.85,
                evidence=f"SHIKARI FII/DII OI: {evidence}",
                timestamp_ms=int(time.time() * 1000),
            )

        verdict = "HOLD"
        confidence = 0.5

        if status == "FAIL":
            if direction == "SELL" and swarm_state.side == "BUY":
                verdict = "EXIT_REVERSAL"
                confidence = 0.95
            elif direction == "BUY" and swarm_state.side == "SELL":
                verdict = "EXIT_REVERSAL"
                confidence = 0.95
            elif direction == "HALT":
                verdict = "EXIT_RISK"
                confidence = 0.99
            elif direction in {"BEARISH_MACRO", "COUNTER_REGIME_BLOCK", "CHOP_REGIME_BLOCK"}:
                verdict = "EXIT_REGIME"
                confidence = 0.90
        elif status == "PASS" and direction == swarm_state.side:
            verdict = "HOLD"
            confidence = 0.85
        elif status == "WARN":
            verdict = "CAUTION"
            confidence = 0.60

        if swarm_state.side == "BUY":
            if market_data.get("ltp", 0) >= swarm_state.take_profit:
                verdict = "EXIT_TP"
                confidence = 1.0
            elif market_data.get("ltp", 0) <= swarm_state.trailing_sl:
                verdict = "EXIT_TSL"
                confidence = 1.0
            elif market_data.get("ltp", 0) >= swarm_state.take_profit * 0.92:
                if direction in {"SELL", "BEARISH_MACRO", "COUNTER_REGIME_BLOCK"}:
                    verdict = "EXIT_EARLY"
                    confidence = 0.92
        elif swarm_state.side == "SELL":
            if market_data.get("ltp", 0) <= swarm_state.take_profit:
                verdict = "EXIT_TP"
                confidence = 1.0
            elif market_data.get("ltp", 0) >= swarm_state.trailing_sl:
                verdict = "EXIT_TSL"
                confidence = 1.0
            elif market_data.get("ltp", 0) <= swarm_state.take_profit * 1.08:
                if direction in {"BUY", "BEARISH_MACRO", "COUNTER_REGIME_BLOCK"}:
                    verdict = "EXIT_EARLY"
                    confidence = 0.92

        return AgentVerdict(
            agent_id=self.agent_id,
            engine_id=self.engine_focus,
            category=engine.get("label", "unknown"),
            verdict=verdict,
            confidence=confidence,
            evidence=evidence,
            timestamp_ms=int(time.time() * 1000),
        )


class AISwarmSupervision:
    def __init__(
        self,
        database: DatabaseHub,
        redis: RedisCache,
    ):
        self.database = database
        self.redis = redis
        self.swarms: dict[str, SwarmState] = {}
        self.agent_pool: list[AISwarmAgent] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._supervision_task: asyncio.Task | None = None
        self._initialize_agents()

    def _initialize_agents(self):
        engine_ids = [
            "ENG01_AMT_TPO",
            "ENG02_BREADTH",
            "ENG03_SECTOR_RS",
            "ENG04_INTERMARKET_MACRO",
            "ENG05_TAPE_READING",
            "ENG06_LIQUIDITY_HEATMAP",
            "ENG07_OPTIONS_FLOW",
            "ENG08_VOL_SURFACE",
            "ENG09_GEX_PINNING",
            "ENG10_EVENT_CATALYST",
            "ENG11_NEWS_SENTIMENT",
            "ENG12_REGIME_ROUTER",
            "ENG13_LEAD_LAG_CORR",
            "ENG14_PORTFOLIO_EXPOSURE",
            "ENG15_EXECUTION_SOR",
            "ENG16_ADAPTIVE_SIZING",
            "ENG17_TRADE_LIFECYCLE",
            "ENG18_DRIFT_MONITOR",
            "ENG19_WF_ROBUSTNESS",
            "ENG20_FEED_CONSENSUS",
            "ENG21_SHIKARI_SWEEP_RECLAIM",
            "ENG22_SHIKARI_WHALE_ABSORPTION",
            "ENG23_SHIKARI_DUMP_SHIELD",
            "ENG24_SHIKARI_FII_DII_OI",
            "ENG25_SHIKARI_HUNTER_UNWIND",
        ]

        for i in range(AGENT_COUNT):
            engine_focus = engine_ids[i % len(engine_ids)]
            self.agent_pool.append(AISwarmAgent(i, engine_focus))

    async def start_swarm_for_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        trailing_sl: float,
        quantity: float,
        exchange: str,
        broker_token: str,
        is_auto_trade: bool,
    ) -> SwarmState:
        async with self._lock:
            if order_id in self.swarms:
                return self.swarms[order_id]

            state = SwarmState(
                order_id=order_id,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_sl=trailing_sl,
                quantity=quantity,
                exchange=exchange,
                broker_token=broker_token,
                is_auto_trade=is_auto_trade,
                created_at=datetime.now(timezone.utc),
            )
            self.swarms[order_id] = state

            if not self._running:
                self._running = True
                self._supervision_task = asyncio.create_task(self._supervision_loop())

            logger.info(
                "AI Swarm activated for order %s (%s %s) - %d agents monitoring",
                order_id, side, symbol, AGENT_COUNT
            )
            return state

    async def terminate_swarm(self, order_id: str, reason: str = "CLOSED") -> bool:
        async with self._lock:
            if order_id not in self.swarms:
                return False

            state = self.swarms[order_id]
            state.is_terminated = True
            state.agents_active = 0
            state.last_verdict = f"TERMINATED_{reason}"

            del self.swarms[order_id]

            if not self.swarms:
                self._running = False
                if self._supervision_task:
                    self._supervision_task.cancel()
                    try:
                        await self._supervision_task
                    except asyncio.CancelledError:
                        pass
                    self._supervision_task = None

            logger.info(
                "AI Swarm TERMINATED for order %s - %s - agents released: %d",
                order_id, reason, AGENT_COUNT
            )
            return True

    async def update_swarm_state(
        self,
        order_id: str,
        trailing_sl: float | None = None,
        market_price: float | None = None,
    ) -> bool:
        async with self._lock:
            if order_id not in self.swarms:
                return False

            state = self.swarms[order_id]
            if trailing_sl is not None:
                state.trailing_sl = trailing_sl
            if market_price is not None:
                state.last_tick_ms = int(time.time() * 1000)
            return True

    async def get_swarm_status(self, order_id: str) -> dict[str, Any] | None:
        async with self._lock:
            state = self.swarms.get(order_id)
            if not state:
                return None
            return {
                "order_id": state.order_id,
                "symbol": state.symbol,
                "side": state.side,
                "agents_active": state.agents_active,
                "consensus_score": state.last_consensus_score,
                "verdict": state.last_verdict,
                "last_tick_ms": state.last_tick_ms,
                "is_terminated": state.is_terminated,
                "entry_price": state.entry_price,
                "current_trailing_sl": state.trailing_sl,
                "take_profit": state.take_profit,
                "created_at": state.created_at.isoformat(),
            }

    async def list_active_swarms(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [await self.get_swarm_status(oid) for oid in self.swarms.keys()]

    async def _supervision_loop(self):
        logger.info("AI Swarm supervision loop started - monitoring %d swarms", len(self.swarms))

        while self._running and self.swarms:
            try:
                await self._tick_all_swarms()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("AI Swarm supervision loop error: %s", exc)

            await asyncio.sleep(SWARM_TICK_INTERVAL)

        logger.info("AI Swarm supervision loop stopped")

    async def _tick_all_swarms(self):
        async with self._lock:
            active_swarms = {oid: state for oid, state in self.swarms.items() if not state.is_terminated}

        if not active_swarms:
            return

        for order_id, state in active_swarms.items():
            try:
                await self._process_swarm_tick(state)
            except Exception as exc:
                logger.exception("Error processing swarm for order %s: %s", order_id, exc)

    async def _process_swarm_tick(self, state: SwarmState):
        price_feed = get_price_feed_manager()
        quote = await price_feed.get_quote(state.symbol, state.exchange)

        if not quote or quote.price <= 0:
            logger.warning("No valid quote for %s/%s in swarm", state.exchange, state.symbol)
            return

        market_price = quote.price
        state.last_tick_ms = int(time.time() * 1000)

        candles = await price_feed.get_candles(state.symbol, state.exchange, "15m", 120)
        closed_bars = [bar for bar in candles if bar.get("is_closed")]
        quant_features = analyze_quant_features(candles)

        engine_payload = {
            "symbol": state.symbol,
            "order_key": state.symbol,
            "category": "indian" if state.exchange in {"NSE", "BSE", "NFO", "BFO"} else "crypto",
            "base_side": state.side,
            "ltp": market_price,
            "sl": state.trailing_sl,
            "target": state.take_profit,
            "candles": closed_bars,
            "universe": [],
            "news_items": [],
            "macro": {},
            "data_source": quote.source,
            "data_quality": quote.quality,
            "event_time_ms": int(time.time() * 1000),
        }

        engine_results = evaluate_engine_suite(engine_payload)["engines"]

        news_engine = NewsEngine.__new__(NewsEngine)
        news_snapshot = {"items": [], "providers": {}}
        news_items = news_snapshot.get("items", [])
        macro_data = {}

        verdicts = await asyncio.gather(*[
            agent.evaluate(state, {"ltp": market_price}, engine_results, news_items, macro_data, quant_features)
            for agent in self.agent_pool
        ])

        # SHIKARI IMMEDIATE OVERRIDE: Hunter Unwind or Dump Shield triggers INSTANT exit (no consensus needed)
        hunter_unwind_verdicts = [v for v in verdicts if v.verdict == "EXIT_HUNTER_UNWIND"]
        dump_shield_verdicts = [v for v in verdicts if v.verdict == "EXIT_DUMP_SHIELD"]
        
        if hunter_unwind_verdicts:
            state.last_verdict = "SHIKARI_HUNTER_UNWIND_IMMEDIATE"
            logger.critical(
                "🚨 SHIKARI HUNTER UNWIND TRIGGERED: Order %s (%s %s) - IMMEDIATE EXIT at %.2f | Evidence: %s",
                state.order_id, state.side, state.symbol, market_price, hunter_unwind_verdicts[0].evidence
            )
            await self._execute_consensus_exit(state, market_price, "SHIKARI_HUNTER_UNWIND")
            return
            
        if dump_shield_verdicts:
            state.last_verdict = "SHIKARI_DUMP_SHIELD_IMMEDIATE"
            logger.critical(
                "🚨 SHIKARI DUMP SHIELD TRIGGERED: Order %s (%s %s) - IMMEDIATE EXIT at %.2f | Evidence: %s",
                state.order_id, state.side, state.symbol, market_price, dump_shield_verdicts[0].evidence
            )
            await self._execute_consensus_exit(state, market_price, "SHIKARI_DUMP_SHIELD")
            return

        exit_verdicts = [v for v in verdicts if v.verdict.startswith("EXIT")]
        exit_count = len(exit_verdicts)
        consensus_score = exit_count / AGENT_COUNT

        state.last_consensus_score = consensus_score
        state.agents_active = AGENT_COUNT

        if consensus_score >= CONSENSUS_THRESHOLD:
            state.last_verdict = "CONSENSUS_EXIT"
            logger.critical(
                "AI SWARM 100%% CONSENSUS EXIT: Order %s (%s %s) - %d/%d agents voted EXIT at price %.2f",
                state.order_id, state.side, state.symbol, exit_count, AGENT_COUNT, market_price
            )
            await self._execute_consensus_exit(state, market_price, verdicts[0].verdict)
        elif consensus_score >= 0.80:
            state.last_verdict = "HIGH_CONSENSUS_WARNING"
            logger.warning(
                "AI Swarm HIGH CONSENSUS: Order %s - %.0f%% agents signal exit (%d/%d)",
                state.order_id, consensus_score * 100, exit_count, AGENT_COUNT
            )
        elif consensus_score >= 0.50:
            state.last_verdict = "MODERATE_CONSENSUS"
        else:
            state.last_verdict = "HOLD"

        if state.side == "BUY":
            if market_price >= state.take_profit:
                await self._execute_consensus_exit(state, market_price, "EXIT_TP")
            elif market_price <= state.trailing_sl:
                await self._execute_consensus_exit(state, market_price, "EXIT_TSL")
        elif state.side == "SELL":
            if market_price <= state.take_profit:
                await self._execute_consensus_exit(state, market_price, "EXIT_TP")
            elif market_price >= state.trailing_sl:
                await self._execute_consensus_exit(state, market_price, "EXIT_TSL")

    async def _execute_consensus_exit(self, state: SwarmState, exit_price: float, reason: str):
        state.is_terminated = True
        state.last_verdict = reason
        state.agents_active = 0

        logger.critical(
            "EXECUTING AI SWARM CONSENSUS EXIT: Order=%s Side=%s Symbol=%s Price=%.2f Reason=%s",
            state.order_id, state.side, state.symbol, exit_price, reason
        )

        await self.terminate_swarm(state.order_id, reason)


async def get_ai_swarm_service(app) -> AISwarmSupervision:
    if not hasattr(app.state, "ai_swarm"):
        app.state.ai_swarm = AISwarmSupervision(
            database=app.state.database,
            redis=app.state.redis,
        )
    return app.state.ai_swarm
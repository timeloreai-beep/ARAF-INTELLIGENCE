from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class PriceTick:
    """Normalized price tick from any feed."""
    symbol: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    volume_24h: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    change_24h_pct: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "UNKNOWN"
    quality: str = "LIVE"  # LIVE, STALE, OFFLINE
    exchange: str = ""
    raw_data: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "volume_24h": self.volume_24h,
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "change_24h_pct": self.change_24h_pct,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "quality": self.quality,
            "exchange": self.exchange,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PriceTick":
        ts = data.get("timestamp")
        if isinstance(ts, str):
            timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            timestamp = datetime.now(timezone.utc)
        return cls(
            symbol=data["symbol"],
            price=data["price"],
            bid=data.get("bid"),
            ask=data.get("ask"),
            bid_size=data.get("bid_size"),
            ask_size=data.get("ask_size"),
            volume_24h=data.get("volume_24h"),
            high_24h=data.get("high_24h"),
            low_24h=data.get("low_24h"),
            change_24h_pct=data.get("change_24h_pct"),
            timestamp=timestamp,
            source=data.get("source", "UNKNOWN"),
            quality=data.get("quality", "LIVE"),
            exchange=data.get("exchange", ""),
            raw_data=data.get("raw_data"),
        )


class FeedWorker(ABC):
    """Abstract base for market data feed workers."""

    def __init__(self, name: str, symbols: List[str]):
        self.name = name
        self.symbols = symbols
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_tick: Dict[str, datetime] = {}
        self._tick_count = 0
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._connected = False
        self._on_tick: Optional[Callable[[PriceTick], asyncio.Awaitable[None]]] = None
        self._on_status_change: Optional[Callable[[str, bool], asyncio.Awaitable[None]]] = None

    def set_callbacks(
        self,
        on_tick: Callable[[PriceTick], asyncio.Awaitable[None]],
        on_status_change: Callable[[str, bool], asyncio.Awaitable[None]],
    ):
        self._on_tick = on_tick
        self._on_status_change = on_status_change

    @abstractmethod
    async def _run(self) -> None:
        """Main feed loop - implement in subclass."""

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        await self._notify_status(True)
        logger.info("Feed worker started: %s (%d symbols)", self.name, len(self.symbols))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._notify_status(False)
        logger.info("Feed worker stopped: %s", self.name)

    def is_healthy(self) -> bool:
        return self._running and self._connected and self._error_count < 10

    def get_stats(self) -> dict:
        now = datetime.now(timezone.utc)
        ages = {
            sym: (now - ts).total_seconds()
            for sym, ts in self._last_tick.items()
        }
        return {
            "name": self.name,
            "running": self._running,
            "connected": self._connected,
            "symbols": self.symbols,
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "last_tick_age_seconds": ages,
        }

    async def _emit_tick(self, tick: PriceTick) -> None:
        if self._on_tick:
            try:
                await self._on_tick(tick)
            except Exception as e:
                logger.warning("Tick callback failed for %s: %s", self.name, e)

        self._last_tick[tick.symbol] = tick.timestamp
        self._tick_count += 1

    async def _notify_status(self, connected: bool) -> None:
        self._connected = connected
        if self._on_status_change:
            try:
                await self._on_status_change(self.name, connected)
            except Exception as e:
                logger.warning("Status callback failed for %s: %s", self.name, e)

    def _record_error(self, error: str) -> None:
        self._error_count += 1
        self._last_error = error
        logger.warning("Feed %s error (%d): %s", self.name, self._error_count, error)


class BinanceSpotFeedWorker(FeedWorker):
    """Binance Spot WebSocket feed for crypto assets."""

    BASE_URL = "wss://stream.binance.com:9443/ws"
    RECONNECT_DELAY = 2.0
    MAX_RECONNECT_DELAY = 30.0
    PING_INTERVAL = 20.0

    def __init__(self, symbols: List[str]):
        super().__init__("binance_spot", symbols)
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._reconnect_delay = self.RECONNECT_DELAY
        self._ping_task: Optional[asyncio.Task] = None

    def _build_stream_name(self, symbol: str) -> str:
        """Convert BTCUSDT -> btcusdt@ticker"""
        return f"{symbol.lower()}@ticker"

    async def _run(self) -> None:
        self._session = aiohttp.ClientSession()
        while self._running:
            try:
                streams = "/".join(self._build_stream_name(s) for s in self.symbols)
                url = f"{self.BASE_URL}/{streams}"
                async with self._session.ws_connect(
                    url,
                    heartbeat=self.PING_INTERVAL,
                    max_msg_size=10 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = self.RECONNECT_DELAY
                    await self._notify_status(True)
                    logger.info("Binance WS connected: %d streams", len(self.symbols))

                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(msg.data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            raise RuntimeError(f"WS error: {ws.exception()}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING):
                            break

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(str(e))
                await self._notify_status(False)
                if self._running:
                    delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)
                    logger.info("Binance WS reconnecting in %.1fs", delay)
                    await asyncio.sleep(delay)
                    self._reconnect_delay = delay
            finally:
                if self._ws:
                    await self._ws.close()
                self._ws = None
                await self._notify_status(False)

    async def _handle_message(self, data: str) -> None:
        try:
            msg = json.loads(data)
            # Binance combined stream format: {"stream": "btcusdt@ticker", "data": {...}}
            if "stream" in msg and "data" in msg:
                tick_data = msg["data"]
                stream = msg["stream"]
                symbol = stream.split("@")[0].upper()
            else:
                tick_data = msg
                symbol = tick_data.get("s", "").upper()

            if not symbol or symbol not in self.symbols:
                return

            tick = PriceTick(
                symbol=symbol,
                price=float(tick_data.get("c", 0)),
                bid=float(tick_data.get("b", 0)) or None,
                ask=float(tick_data.get("a", 0)) or None,
                bid_size=float(tick_data.get("B", 0)) or None,
                ask_size=float(tick_data.get("A", 0)) or None,
                volume_24h=float(tick_data.get("v", 0)) or None,
                high_24h=float(tick_data.get("h", 0)) or None,
                low_24h=float(tick_data.get("l", 0)) or None,
                change_24h_pct=float(tick_data.get("P", 0)) or None,
                source="BINANCE_SPOT_WS",
                exchange="BINANCE",
            )
            await self._emit_tick(tick)

        except Exception as e:
            self._record_error(f"Message parse error: {e}")

    async def stop(self) -> None:
        await super().stop()
        if self._session:
            await self._session.close()
            self._session = None


class CoinDCXFeedWorker(FeedWorker):
    """CoinDCX REST polling feed (fallback when WS unavailable)."""

    BASE_URL = "https://api.coindcx.com/exchange/ticker"
    POLL_INTERVAL = 2.0  # seconds

    def __init__(self, symbols: List[str]):
        super().__init__("coindcx", symbols)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _run(self) -> None:
        self._session = aiohttp.ClientSession()
        await self._notify_status(True)
        logger.info("CoinDCX poller started: %d symbols", len(self.symbols))

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(str(e))
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll_once(self) -> None:
        if not self._session:
            return
        async with self._session.get(self.BASE_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CoinDCX API error: {resp.status}")
            data = await resp.json()
            if not isinstance(data, list):
                return

            now = datetime.now(timezone.utc)
            for item in data:
                symbol = item.get("market", "").upper()
                if symbol not in self.symbols:
                    continue
                tick = PriceTick(
                    symbol=symbol,
                    price=float(item.get("last_price", 0)),
                    bid=float(item.get("bid", 0)) or None,
                    ask=float(item.get("ask", 0)) or None,
                    volume_24h=float(item.get("volume", 0)) or None,
                    high_24h=float(item.get("high", 0)) or None,
                    low_24h=float(item.get("low", 0)) or None,
                    change_24h_pct=float(item.get("change_24_hour", 0)) or None,
                    timestamp=now,
                    source="COINDCX_REST",
                    exchange="COINDCX",
                )
                await self._emit_tick(tick)

    async def stop(self) -> None:
        await super().stop()
        if self._session:
            await self._session.close()
            self._session = None


class ShoonyaFeedWorker(FeedWorker):
    """
    Shoonya (Finvasia) REST polling feed - OPTIMIZED 1s batch polling.
    
    TECHNICAL LIMITATION: Shoonya/Finvasia API does NOT provide WebSocket for quotes.
    Only REST GetQuotes is available. Best achievable: ~1s batch polling.
    """

    def __init__(self, symbols: List[str]):
        super().__init__("shoonya", symbols)
        self._poll_interval = 1.0  # 1 second - much faster than 5s legacy
        self._session_token: Optional[str] = None
        self._user_id: Optional[str] = None

    def set_session(self, user_id: str, session_token: str):
        """Set Shoonya session from existing authenticated session."""
        self._user_id = user_id
        self._session_token = session_token

    async def _run(self) -> None:
        await self._notify_status(True)
        logger.info("Shoonya poller started: %d symbols (%.1fs interval) - REST GetQuotes only", len(self.symbols), self._poll_interval)

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._record_error(str(e))
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        if not self._session_token or not self._user_id:
            # No authenticated session available - wait for one
            return

        # Group symbols by exchange (NSE for indices/equities, NFO for options)
        by_exchange: Dict[str, List[str]] = {}
        for sym in self.symbols:
            if sym in {"NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"}:
                ex = "NSE"
            elif sym.endswith("CE") or sym.endswith("PE"):
                ex = "NFO"
            else:
                ex = "NSE"
            by_exchange.setdefault(ex, []).append(sym)

        # Fetch quotes via Shoonya REST API using existing session
        try:
            import aiohttp
            from urllib.parse import urlencode

            # Shoonya GetQuotes endpoint
            base_url = "https://api.shoonya.com/NorenWClientTP/GetQuotes"
            async with aiohttp.ClientSession() as session:
                for exchange, syms in by_exchange.items():
                    # Batch symbols in groups of 50 (Shoonya limit)
                    for i in range(0, len(syms), 50):
                        batch = syms[i:i+50]
                        payload = {
                            "uid": self._user_id,
                            "actid": self._user_id,
                            "susertoken": self._session_token,
                            "exch": exchange,
                            "tsym": ",".join(batch),
                        }
                        async with session.post(base_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            if not isinstance(data, list):
                                continue
                            now = datetime.now(timezone.utc)
                            for item in data:
                                tsym = item.get("tsym", "").upper()
                                lp = float(item.get("lp", 0))
                                if not tsym or lp <= 0:
                                    continue
                                tick = PriceTick(
                                    symbol=tsym,
                                    price=lp,
                                    bid=float(item.get("bp", 0)) or None,
                                    ask=float(item.get("ap", 0)) or None,
                                    bid_size=float(item.get("bq", 0)) or None,
                                    ask_size=float(item.get("aq", 0)) or None,
                                    volume_24h=float(item.get("v", 0)) or None,
                                    high_24h=float(item.get("h", 0)) or None,
                                    low_24h=float(item.get("l", 0)) or None,
                                    change_24h_pct=float(item.get("pc", 0)) or None,
                                    timestamp=now,
                                    source="SHOONYA_REST_1S",
                                    exchange=exchange,
                                )
                                await self._emit_tick(tick)
        except Exception as e:
            self._record_error(f"Shoonya poll error: {e}")


class PriceBus:
    """Central price distribution bus using asyncio queues."""

    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._latest: Dict[str, PriceTick] = {}
        self._stats = {
            "total_ticks": 0,
            "by_source": {},
            "by_symbol": {},
        }

    def subscribe(self, symbols: Set[str]) -> asyncio.Queue:
        """Create a new subscriber queue for given symbols."""
        queue = asyncio.Queue(maxsize=1000)
        for sym in symbols:
            self._subscribers.setdefault(sym, []).append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        for subs in self._subscribers.values():
            if queue in subs:
                subs.remove(queue)

    async def publish(self, tick: PriceTick) -> None:
        self._latest[tick.symbol] = tick
        self._stats["total_ticks"] += 1
        self._stats["by_source"][tick.source] = self._stats["by_source"].get(tick.source, 0) + 1
        self._stats["by_symbol"][tick.symbol] = self._stats["by_symbol"].get(tick.symbol, 0) + 1

        queues = self._subscribers.get(tick.symbol, [])
        for q in queues:
            if not q.full():
                q.put_nowait(tick)

    def get_latest(self, symbol: str) -> Optional[PriceTick]:
        return self._latest.get(symbol)

    def get_all_latest(self) -> Dict[str, PriceTick]:
        return dict(self._latest)

    def get_stats(self) -> dict:
        return dict(self._stats)


class PriceFeedManager:
    """Manages all feed workers and the price bus."""

    def __init__(self):
        self._workers: List[FeedWorker] = []
        self._bus = PriceBus()
        self._running = False
        self._freshness_task: Optional[asyncio.Task] = None
        self._shoonya_worker: Optional[ShoonyaFeedWorker] = None

    async def start(self, symbols_config: Dict[str, List[str]]) -> None:
        if self._running:
            return
        self._running = True

        # Create workers based on config
        # Binance Spot WS for crypto
        if "crypto" in symbols_config:
            worker = BinanceSpotFeedWorker(symbols_config["crypto"])
            worker.set_callbacks(self._bus.publish, self._on_worker_status)
            self._workers.append(worker)
            await worker.start()

        # CoinDCX REST polling for crypto (redundant)
        if "crypto" in symbols_config:
            worker = CoinDCXFeedWorker(symbols_config["crypto"])
            worker.set_callbacks(self._bus.publish, self._on_worker_status)
            self._workers.append(worker)
            await worker.start()

        # Shoonya REST polling for India (1s batch) - NO WebSocket available from Finvasia
        if "india" in symbols_config:
            self._shoonya_worker = ShoonyaFeedWorker(symbols_config["india"])
            self._shoonya_worker.set_callbacks(self._bus.publish, self._on_worker_status)
            self._workers.append(self._shoonya_worker)
            await self._shoonya_worker.start()

        # Start freshness monitor
        self._freshness_task = asyncio.create_task(self._freshness_monitor())
        logger.info("PriceFeedManager started with %d workers", len(self._workers))

    def set_shoonya_session(self, user_id: str, session_token: str) -> None:
        """Set Shoonya session for authenticated polling."""
        if self._shoonya_worker:
            self._shoonya_worker.set_session(user_id, session_token)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for worker in self._workers:
            await worker.stop()
        if self._freshness_task:
            self._freshness_task.cancel()
            try:
                await self._freshness_task
            except asyncio.CancelledError:
                pass
        logger.info("PriceFeedManager stopped")

    def get_bus(self) -> PriceBus:
        return self._bus

    def get_worker_stats(self) -> List[dict]:
        return [w.get_stats() for w in self._workers]

    async def _on_worker_status(self, name: str, connected: bool) -> None:
        logger.info("Feed worker %s: %s", name, "CONNECTED" if connected else "DISCONNECTED")

    async def _freshness_monitor(self) -> None:
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                stale_threshold = 15.0  # seconds
                offline_threshold = 60.0

                for symbol, tick in self._bus._latest.items():
                    age = (now - tick.timestamp).total_seconds()
                    if age > offline_threshold and tick.quality != "OFFLINE":
                        tick.quality = "OFFLINE"
                        await self._bus.publish(tick)
                    elif age > stale_threshold and tick.quality == "LIVE":
                        tick.quality = "STALE"
                        await self._bus.publish(tick)
            except Exception as e:
                logger.warning("Freshness monitor error: %s", e)
            await asyncio.sleep(3.0)


# Global manager instance
_price_feed_manager: Optional[PriceFeedManager] = None


def get_price_feed_manager() -> PriceFeedManager:
    global _price_feed_manager
    if _price_feed_manager is None:
        _price_feed_manager = PriceFeedManager()
    return _price_feed_manager
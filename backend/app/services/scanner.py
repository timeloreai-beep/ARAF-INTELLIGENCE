from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
import time

import aiohttp

logger = logging.getLogger(__name__)


class UniverseScanner:
    def __init__(self, concurrency: int = 40) -> None:
        self.concurrency = concurrency
        self._universe_cache: dict[str, tuple[float, list[dict]]] = {}

    async def scan_india_tradingview(self, max_items: int = 5000) -> list[dict]:
        cache_key = f"india:{max_items}"
        cached = self._universe_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 60:
            return [dict(item) for item in cached[1]]
        all_items: list[dict] = []
        async with aiohttp.ClientSession() as session:
            chunk = 500
            for start in range(0, max_items, chunk):
                payload = {
                    "filter": [{"left": "exchange", "operation": "in_range", "right": ["NSE", "BSE"]}],
                    "options": {"lang": "en"},
                    "markets": ["india"],
                    "symbols": {"query": {"types": ["stock"]}, "tickers": []},
                    "columns": [
                        "name", "description", "exchange", "close", "change", "volume",
                        "relative_volume_10d_calc", "ATR", "RSI", "EMA20", "EMA50",
                        "sector", "high", "low"
                    ],
                    "sort": {"sortBy": "volume", "sortOrder": "desc"},
                    "range": [start, min(start + chunk - 1, max_items - 1)],
                }
                try:
                    async with session.post(
                        "https://scanner.tradingview.com/india/scan",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status != 200:
                            break
                        data = await response.json()
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    break
                rows = data.get("data", [])
                if not rows:
                    break
                for row in rows:
                    values = row.get("d", [])
                    if len(values) < 14:
                        continue
                    ticker = str(row.get("s", "")).upper()
                    exchange = str(values[2] or ("BSE" if ticker.startswith("BSE:") else "NSE")).upper()
                    symbol = str(values[0] or ticker.split(":")[-1]).upper()
                    price = float(values[3] or 0)
                    volume = float(values[5] or 0)
                    all_items.append({
                        "symbol": symbol,
                        "exchange": exchange,
                        "name": str(values[1] or symbol),
                        "price": price,
                        "change_pct": float(values[4] or 0),
                        "volume": volume,
                        "rvol": values[6],
                        "atr": values[7],
                        "rsi": values[8],
                        "sector": str(values[11] or "Unclassified"),
                        "high": float(values[12] or price),
                        "low": float(values[13] or price),
                    })
        sem = asyncio.Semaphore(self.concurrency)

        async def validated(item: dict) -> dict | None:
            async with sem:
                if item["price"] <= 0 or item["volume"] < 25000:
                    item["eligible"] = False
                    item["skip_reason"] = "INVALID_PRICE_OR_LIQUIDITY"
                else:
                    item["eligible"] = True
                return item

        results = await asyncio.gather(*(validated(item) for item in all_items))
        universe = [item for item in results if item is not None]
        self._universe_cache[cache_key] = (time.monotonic(), universe)
        return [dict(item) for item in universe]

    async def scan_crypto(self, limit: int = 1000) -> list[dict]:
        cache_key = f"crypto:{limit}"
        cached = self._universe_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 30:
            return [dict(item) for item in cached[1]]

        providers = (
            ("binance", self._scan_crypto_binance),
            ("coingecko", self._scan_crypto_coingecko),
            ("coinpaprika", self._scan_crypto_coinpaprika),
        )
        last_error: Exception | None = None
        for name, fetch in providers:
            try:
                universe = await fetch(limit)
            except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError) as exc:
                last_error = exc
                logger.warning("Crypto scanner provider %s failed: %s", name, exc)
                continue
            if not universe:
                logger.warning("Crypto scanner provider %s returned no usable markets", name)
                continue
            self._universe_cache[cache_key] = (time.monotonic(), universe)
            return [dict(item) for item in universe]

        message = "Crypto market data unavailable from Binance, CoinGecko, and Coinpaprika"
        if last_error:
            raise RuntimeError(message) from last_error
        raise RuntimeError(message)

    async def scan_crypto_binance(self, limit: int = 1000) -> list[dict]:
        return await self.scan_crypto(limit)

    async def _scan_crypto_binance(self, limit: int) -> list[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                rows = await response.json()
        if not isinstance(rows, list):
            raise ValueError("Binance returned an invalid crypto ticker payload")
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).upper()
            if not symbol.endswith("USDT"):
                continue
            if any(token in symbol for token in ("UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT")):
                continue
            price = self._positive_number(row.get("lastPrice"))
            if price is None:
                continue
            quote_volume = self._finite_number(row.get("quoteVolume"))
            items.append({
                "symbol": symbol,
                "exchange": "COINDCX",
                "price": price,
                "high": self._positive_number(row.get("highPrice")) or price,
                "low": self._positive_number(row.get("lowPrice")) or price,
                "change_pct": self._finite_number(row.get("priceChangePercent")),
                "volume": self._finite_number(row.get("volume")),
                "turnover": quote_volume,
                "eligible": quote_volume >= 5_000_000,
                "data_source": "binance",
            })
        items.sort(key=lambda item: float(item["turnover"]), reverse=True)
        return items[:limit]

    async def _scan_crypto_coingecko(self, limit: int) -> list[dict]:
        page_size = min(max(limit, 1), 250)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": page_size,
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "24h",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                rows = await response.json()
        if not isinstance(rows, list):
            raise ValueError("CoinGecko returned an invalid crypto market payload")

        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            base_symbol = str(row.get("symbol", "")).upper()
            symbol = f"{base_symbol}USDT"
            price = self._positive_number(row.get("current_price"))
            turnover = self._positive_number(row.get("total_volume"))
            if not base_symbol or price is None or turnover is None:
                continue
            if any(token in symbol for token in ("UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT")):
                continue
            items.append({
                "symbol": symbol,
                "exchange": "COINDCX",
                "price": price,
                "high": self._positive_number(row.get("high_24h")) or price,
                "low": self._positive_number(row.get("low_24h")) or price,
                "change_pct": self._finite_number(
                    row.get("price_change_percentage_24h")
                ),
                "volume": turnover / price,
                "turnover": turnover,
                "eligible": turnover >= 5_000_000,
                "data_source": "coingecko",
            })
        items.sort(key=lambda item: float(item["turnover"]), reverse=True)
        return items[:limit]

    async def _scan_crypto_coinpaprika(self, limit: int) -> list[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.coinpaprika.com/v1/tickers",
                params={"quotes": "USD"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                rows = await response.json()
        if not isinstance(rows, list):
            raise ValueError("Coinpaprika returned an invalid crypto ticker payload")

        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            base_symbol = str(row.get("symbol", "")).upper()
            symbol = f"{base_symbol}USDT"
            quotes = row.get("quotes")
            usd = quotes.get("USD") or {} if isinstance(quotes, dict) else {}
            if not isinstance(usd, dict):
                continue
            price = self._positive_number(usd.get("price"))
            turnover = self._positive_number(usd.get("volume_24h"))
            if not base_symbol or price is None or turnover is None:
                continue
            if any(token in symbol for token in ("UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT")):
                continue
            items.append({
                "symbol": symbol,
                "exchange": "COINDCX",
                "price": price,
                "high": price,
                "low": price,
                "change_pct": self._finite_number(usd.get("percent_change_24h")),
                "volume": turnover / price,
                "turnover": turnover,
                "eligible": turnover >= 5_000_000,
                "data_source": "coinpaprika",
            })
        items.sort(key=lambda item: float(item["turnover"]), reverse=True)
        return items[:limit]

    @staticmethod
    def _positive_number(value) -> float | None:
        number = UniverseScanner._finite_number(value)
        return number if number > 0 else None

    @staticmethod
    def _finite_number(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) else 0.0

    async def get_crypto_closed_candles(
        self,
        symbol: str,
        *,
        interval: str = "15m",
        limit: int = 120,
    ) -> list[dict]:
        if interval not in {"5m", "15m", "1h"}:
            raise ValueError("Unsupported crypto candle interval")
        symbol = symbol.upper()
        if not symbol.endswith("USDT") or not symbol.isalnum():
            raise ValueError("A Binance USDT symbol is required")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": min(max(limit, 16), 500)},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as response:
                response.raise_for_status()
                rows = await response.json()

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        candles = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            timestamp, close_time = int(row[0]), int(row[6])
            if close_time >= now_ms:
                continue
            values = [float(row[index]) for index in (1, 2, 3, 4, 5)]
            open_price, high, low, close, volume = values
            if (
                not all(math.isfinite(value) for value in values)
                or min(open_price, high, low, close) <= 0
                or volume < 0
                or low > min(open_price, close)
                or high < max(open_price, close)
            ):
                continue
            candles.append({
                "time": timestamp,
                "close_time": close_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "is_closed": True,
            })
        return candles

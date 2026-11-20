from __future__ import annotations

import asyncio
from typing import Iterable

import aiohttp


class UniverseScanner:
    def __init__(self, concurrency: int = 40) -> None:
        self.concurrency = concurrency

    async def scan_india_tradingview(self, max_items: int = 5000) -> list[dict]:
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
        return [item for item in results if item is not None]

    async def scan_crypto_binance(self, limit: int = 1000) -> list[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                rows = await response.json()
        items = []
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol.endswith("USDT"):
                continue
            if any(token in symbol for token in ("UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT")):
                continue
            price = float(row.get("lastPrice", 0) or 0)
            quote_volume = float(row.get("quoteVolume", 0) or 0)
            if price <= 0:
                continue
            items.append({
                "symbol": symbol,
                "exchange": "COINDCX",
                "price": price,
                "high": float(row.get("highPrice", price) or price),
                "low": float(row.get("lowPrice", price) or price),
                "change_pct": float(row.get("priceChangePercent", 0) or 0),
                "volume": float(row.get("volume", 0) or 0),
                "turnover": quote_volume,
                "eligible": quote_volume >= 5_000_000,
            })
        return items[:limit]

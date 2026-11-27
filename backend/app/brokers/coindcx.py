from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import random
import time
from enum import Enum
from typing import Any

import httpx
from httpx import AsyncBaseTransport

from ..config import get_settings
from .base import BrokerAdapter, BrokerOrderResult, BrokerQuote

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"


class CoinDCXAdapter(BrokerAdapter):
    BASE_URL = "https://api.coindcx.com"
    PUBLIC_PRICE_URL = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"

    MAX_RECONNECT_ATTEMPTS = 10
    BASE_BACKOFF_SECONDS = 5
    MAX_BACKOFF_SECONDS = 60
    WATCHDOG_INTERVAL_SECONDS = 30

    def __init__(self, transport: AsyncBaseTransport | None = None) -> None:
        settings = get_settings()
        self.api_key = settings.coindcx_api_key
        self.api_secret = settings.coindcx_api_secret
        self.transport = transport
        self._connection_state = ConnectionState.DISCONNECTED
        self._reconnect_attempt = 0
        self._last_successful_ping: float | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._watchdog_running = False
        self._lock = asyncio.Lock()

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    @property
    def reconnect_attempt(self) -> int:
        return self._reconnect_attempt

    @property
    def is_connected(self) -> bool:
        return self._connection_state == ConnectionState.CONNECTED

    @property
    def last_successful_ping(self) -> float | None:
        return self._last_successful_ping

    def _calculate_backoff(self) -> float:
        attempt = self._reconnect_attempt
        base = self.BASE_BACKOFF_SECONDS
        max_backoff = self.MAX_BACKOFF_SECONDS
        exponential = min(base * (2 ** attempt), max_backoff)
        jitter = random.uniform(0, exponential * 0.1)
        return exponential + jitter

    def _reset_reconnect_state(self) -> None:
        self._connection_state = ConnectionState.CONNECTED
        self._reconnect_attempt = 0

    async def _test_connectivity(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.get(self.PUBLIC_PRICE_URL)
            response.raise_for_status()
            data = response.json()
            return isinstance(data, (dict, list)) and bool(data)
        except (httpx.HTTPError, OSError, json.JSONDecodeError):
            return False

    async def _authenticated_request(self) -> bool:
        if not self.api_key or not self.api_secret:
            return False
        try:
            payload = {"timestamp": int(time.time() * 1000)}
            signature = self._signature(payload)
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.post(
                    f"{self.BASE_URL}/exchange/v1/users/balances",
                    json=payload,
                    headers={
                        "X-AUTH-APIKEY": self.api_key,
                        "X-AUTH-SIGNATURE": signature,
                    },
                )
            response.raise_for_status()
            return response.status_code < 300
        except (httpx.HTTPError, OSError, RuntimeError):
            return False

    async def login(self) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("CoinDCX credentials are incomplete")
        
        async with self._lock:
            connected = await self._test_connectivity()
            if connected:
                self._reset_reconnect_state()
                self._last_successful_ping = time.monotonic()
                positions = await self.get_positions()
                return {"stat": "Ok", "linked": True, "positions_count": len(positions)}
            else:
                self._connection_state = ConnectionState.DISCONNECTED
                raise RuntimeError("CoinDCX public API connectivity test failed")

    async def smart_reconnect(self) -> bool:
        async with self._lock:
            if self._connection_state == ConnectionState.CONNECTED:
                return True

            self._connection_state = ConnectionState.RECONNECTING
            
            while self._reconnect_attempt < self.MAX_RECONNECT_ATTEMPTS:
                try:
                    connected = await self._test_connectivity()
                    if connected and await self._authenticated_request():
                        self._reset_reconnect_state()
                        self._last_successful_ping = time.monotonic()
                        logger.info("CoinDCX smart reconnect successful")
                        return True
                except (httpx.HTTPError, OSError) as exc:
                    logger.debug("CoinDCX reconnect attempt %d failed: %s", self._reconnect_attempt + 1, exc)

                self._reconnect_attempt += 1
                if self._reconnect_attempt >= self.MAX_RECONNECT_ATTEMPTS:
                    self._connection_state = ConnectionState.MAX_RETRIES_EXCEEDED
                    logger.error("CoinDCX max reconnect attempts (%d) exceeded", self.MAX_RECONNECT_ATTEMPTS)
                    break

                backoff = self._calculate_backoff()
                logger.warning("CoinDCX reconnect attempt %d failed, backing off for %.1fs", self._reconnect_attempt, backoff)
                await asyncio.sleep(backoff)

            return False

    async def ensure_connected(self) -> bool:
        if self._connection_state == ConnectionState.CONNECTED:
            return True
        return await self.smart_reconnect()

    async def start_watchdog(self) -> None:
        if self._watchdog_running:
            return
        self._watchdog_running = True
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("CoinDCX watchdog started")

    async def stop_watchdog(self) -> None:
        self._watchdog_running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("CoinDCX watchdog stopped")

    async def _watchdog_loop(self) -> None:
        while self._watchdog_running:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL_SECONDS)
                if not self._watchdog_running:
                    break

                connected = await self._test_connectivity()
                if connected:
                    self._last_successful_ping = time.monotonic()
                    if self._connection_state != ConnectionState.CONNECTED:
                        logger.info("CoinDCX connectivity restored")
                        if await self._authenticated_request():
                            self._reset_reconnect_state()
                else:
                    if self._connection_state == ConnectionState.CONNECTED:
                        logger.warning("CoinDCX connectivity lost, initiating reconnect")
                        self._connection_state = ConnectionState.DISCONNECTED
                    await self.smart_reconnect()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("CoinDCX watchdog error: %s", exc)
                await asyncio.sleep(5)

    def _signature(self, payload: dict[str, Any]) -> str:
        if not self.api_secret:
            raise RuntimeError("COINDCX_API_SECRET is required")
        body = json.dumps(payload, separators=(",", ":"))
        return hmac.new(self.api_secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    async def get_quote(self, symbol: str, exchange: str = "COINDCX") -> BrokerQuote | None:
        await self.ensure_connected()
        raw_pair = symbol.upper()
        pair = raw_pair.replace("/", "").replace("_", "").replace("-", "")
        if raw_pair.startswith("B-") and raw_pair.endswith("_USDT"):
            market_pair = raw_pair
        else:
            market_pair = f"B-{pair[:-4]}_USDT" if pair.endswith("USDT") else f"B-{pair}_USDT"
        async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
            response = await client.get(self.PUBLIC_PRICE_URL)
        response.raise_for_status()
        data = response.json()
        prices = data.get("prices", data) if isinstance(data, dict) else {}
        quote = None
        if isinstance(prices, dict):
            quote = prices.get(market_pair) or prices.get(pair)
            if quote is None:
                quote = next(
                    (value for key, value in prices.items() if str(key).upper() == market_pair),
                    None,
                )
        price_value = quote.get("last_price", quote.get("price", 0)) if isinstance(quote, dict) else quote
        price = float(price_value or 0)
        if not math.isfinite(price) or price <= 0:
            return None
        return BrokerQuote(market_pair, "COINDCX", price, price, price, source="COINDCX")

    async def place_order(self, **kwargs: Any) -> BrokerOrderResult:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("CoinDCX credentials are incomplete")
        await self.ensure_connected()
        payload = {
            "timestamp": int(time.time() * 1000),
            "order": {
                "side": kwargs["side"].lower(),
                "pair": kwargs["symbol"],
                "total_quantity": kwargs["quantity"],
                "leverage": kwargs.get("leverage", 1),
                "order_type": "market_order",
                "margin_currency_short_name": "USDT",
                "client_order_id": kwargs["client_order_id"],
                "stop_loss_price": kwargs.get("stop_loss"),
                "take_profit_price": kwargs.get("take_profit"),
            },
        }
        signature = self._signature(payload)
        async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
            response = await client.post(
                f"{self.BASE_URL}/exchange/v1/derivatives/futures/orders/create",
                json=payload,
                headers={
                    "X-AUTH-APIKEY": self.api_key,
                    "X-AUTH-SIGNATURE": signature,
                },
            )
        data = response.json()
        order_id = str(data.get("id") or data.get("order_id") or "")
        accepted = response.status_code < 300 and bool(order_id)
        return BrokerOrderResult(
            accepted=accepted,
            broker_order_id=order_id or None,
            status="ACCEPTED" if accepted else "REJECTED",
            message=str(data.get("message", "")),
            raw=data if isinstance(data, dict) else {"response": data},
        )

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("CoinDCX credentials are incomplete")
        await self.ensure_connected()
        payload = {"timestamp": int(time.time() * 1000), "page": "1", "size": "50"}
        signature = self._signature(payload)
        async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
            response = await client.post(
                f"{self.BASE_URL}/exchange/v1/derivatives/futures/positions",
                json=payload,
                headers={
                    "X-AUTH-APIKEY": self.api_key,
                    "X-AUTH-SIGNATURE": signature,
                },
            )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            rows = data.get("positions")
            if isinstance(rows, list):
                return rows
            raise RuntimeError("CoinDCX position request failed")
        if not isinstance(data, list):
            raise ValueError("CoinDCX returned an invalid positions response")
        return data

    async def get_balance(self) -> dict[str, Any]:
        """Get CoinDCX wallet balances (both spot and futures)."""
        if not self.api_key or not self.api_secret:
            raise RuntimeError("CoinDCX credentials are incomplete")
        await self.ensure_connected()
        
        # 1. Get Spot + INR balances from /exchange/v1/users/balances
        spot_balance = 0.0
        inr_balance = 0.0
        payload = {"timestamp": int(time.time() * 1000)}
        signature = self._signature(payload)
        async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
            response = await client.post(
                f"{self.BASE_URL}/exchange/v1/users/balances",
                json=payload,
                headers={
                    "X-AUTH-APIKEY": self.api_key,
                    "X-AUTH-SIGNATURE": signature,
                },
            )
        response.raise_for_status()
        data = response.json()
        
        if isinstance(data, dict):
            balances = data.get("balances", data.get("data", []))
            if isinstance(balances, list):
                for balance in balances:
                    if not isinstance(balance, dict):
                        continue
                    currency = balance.get("currency", "").upper()
                    available = float(balance.get("available_balance", balance.get("available", 0)) or 0)
                    locked = float(balance.get("locked_balance", balance.get("locked", 0)) or 0)
                    total = available + locked
                    
                    if currency == "USDT":
                        spot_balance += total
                    elif currency == "INR":
                        inr_balance += total
        
        # 2. Get Futures wallet balance from /exchange/v1/derivatives/futures/wallets
        futures_balance = 0.0
        payload = {"timestamp": int(time.time() * 1000)}
        signature = self._signature(payload)
        async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
            response = await client.post(
                f"{self.BASE_URL}/exchange/v1/derivatives/futures/wallets",
                json=payload,
                headers={
                    "X-AUTH-APIKEY": self.api_key,
                    "X-AUTH-SIGNATURE": signature,
                },
            )
        response.raise_for_status()
        futures_data = response.json()
        
        if isinstance(futures_data, dict):
            wallets = futures_data.get("wallets", futures_data.get("data", []))
            if isinstance(wallets, list):
                for wallet in wallets:
                    if not isinstance(wallet, dict):
                        continue
                    currency = wallet.get("currency", "").upper()
                    available = float(wallet.get("available_balance", wallet.get("available", 0)) or 0)
                    locked = float(wallet.get("locked_balance", wallet.get("locked", 0)) or 0)
                    total = available + locked
                    if currency == "USDT":
                        futures_balance += total
        
        return {
            "spot_wallet_usdt": spot_balance,
            "futures_wallet_usdt": futures_balance,
            "inr_deposit": inr_balance,
            "linked": True,
            "status": "LIVE",
        }

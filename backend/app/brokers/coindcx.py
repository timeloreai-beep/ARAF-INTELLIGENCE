from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from typing import Any

import httpx

from ..config import get_settings
from .base import BrokerAdapter, BrokerOrderResult, BrokerQuote


class CoinDCXAdapter(BrokerAdapter):
    BASE_URL = "https://api.coindcx.com"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.coindcx_api_key
        self.api_secret = settings.coindcx_api_secret

    def _signature(self, payload: dict[str, Any]) -> str:
        if not self.api_secret:
            raise RuntimeError("COINDCX_API_SECRET is required")
        body = json.dumps(payload, separators=(",", ":"))
        return hmac.new(self.api_secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    async def login(self) -> dict[str, Any]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("CoinDCX credentials are incomplete")
        return {"stat": "Ok", "linked": True}

    async def get_quote(self, symbol: str, exchange: str = "COINDCX") -> BrokerQuote | None:
        raw_pair = symbol.upper()
        pair = raw_pair.replace("/", "").replace("_", "").replace("-", "")
        if raw_pair.startswith("B-") and raw_pair.endswith("_USDT"):
            market_pair = raw_pair
        else:
            market_pair = f"B-{pair[:-4]}_USDT" if pair.endswith("USDT") else f"B-{pair}_USDT"
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
            )
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
        async with httpx.AsyncClient(timeout=12) as client:
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
        payload = {"timestamp": int(time.time() * 1000), "page": "1", "size": "50"}
        signature = self._signature(payload)
        async with httpx.AsyncClient(timeout=12) as client:
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

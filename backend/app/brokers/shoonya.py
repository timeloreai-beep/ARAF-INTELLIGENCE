from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Any

import httpx
import pyotp

from ..config import get_settings
from .base import BrokerAdapter, BrokerOrderResult, BrokerQuote


class ShoonyaAdapter(BrokerAdapter):
    BASE_URL = "https://api.shoonya.com/NorenWClientTP"

    def __init__(self) -> None:
        settings = get_settings()
        self.relay_url = settings.shoonya_relay_url
        self.api_key = settings.shoonya_api_key
        self.user_id = settings.shoonya_user_id
        self.password = settings.shoonya_password
        self.totp_secret = settings.shoonya_totp_secret
        self.vendor_code = settings.shoonya_vendor_code
        self.session_token: str | None = None

    def _totp(self) -> str:
        if not self.totp_secret:
            raise RuntimeError("SHOONYA_TOTP_SECRET is required; TOTP secret is never hard-coded")
        return pyotp.TOTP(self.totp_secret).now()

    async def login(self) -> dict[str, Any]:
        if not all((self.api_key, self.user_id, self.password)):
            raise RuntimeError("Shoonya credentials are incomplete")
        totp = self._totp()
        password_hash = hashlib.sha256(self.password.encode()).hexdigest()
        appkey_hash = hashlib.sha256(f"{self.user_id}|{self.api_key}".encode()).hexdigest()
        vendor_code = self.vendor_code or f"{self.user_id}_U"
        payload = {
            "apkversion": "1.0.0",
            "uid": self.user_id,
            "pwd": password_hash,
            "factor2": totp,
            "vc": vendor_code,
            "appkey": appkey_hash,
            "imei": "araf_terminal_pro",
            "source": "API",
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"{self.BASE_URL}/QuickAuth",
                data={"jData": json.dumps(payload)},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Shoonya returned an invalid authentication response")
        if data.get("stat") != "Ok" or not data.get("susertoken"):
            raise RuntimeError("Shoonya authentication failed; check broker credentials and TOTP")
        self.session_token = data["susertoken"]
        return data

    async def get_quote(self, symbol: str, exchange: str) -> BrokerQuote | None:
        if not self.session_token:
            await self.login()
        payload = json.dumps({"uid": self.user_id, "exch": exchange, "token": symbol})
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"{self.BASE_URL}/GetQuotes",
                data={"jData": payload, "jKey": self.session_token or ""},
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Shoonya returned an invalid quote response")
        price = float(data.get("lp", 0) or 0)
        if data.get("stat") != "Ok" or not math.isfinite(price) or price <= 0:
            return None
        high = float(data.get("h", price) or price)
        low = float(data.get("l", price) or price)
        volume = float(data.get("vol", 0) or 0)
        if not all(math.isfinite(value) for value in (high, low, volume)):
            raise ValueError("Shoonya returned invalid quote values")
        return BrokerQuote(
            symbol=symbol,
            exchange=exchange,
            price=price,
            high=high,
            low=low,
            volume=volume,
            source="SHOONYA",
        )

    async def place_order(self, **kwargs: Any) -> BrokerOrderResult:
        if not self.session_token:
            await self.login()
        jdata = {
            "uid": self.user_id,
            "actid": self.user_id,
            "exch": kwargs["exchange"],
            "tsym": kwargs["symbol"],
            "qty": str(kwargs["quantity"]),
            "prc": "0",
            "prd": "I",
            "trantype": "B" if kwargs["side"] == "BUY" else "S",
            "prctyp": "MKT",
            "ret": "DAY",
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"{self.BASE_URL}/PlaceOrder",
                data={"jData": json.dumps(jdata), "jKey": self.session_token or ""},
            )
        data = response.json()
        accepted = data.get("stat") == "Ok"
        return BrokerOrderResult(
            accepted=accepted,
            broker_order_id=data.get("norenordno"),
            status="ACCEPTED" if accepted else "REJECTED",
            message=data.get("emsg", ""),
            raw=data,
        )

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self.session_token:
            await self.login()
        payload = json.dumps({"uid": self.user_id, "actid": self.user_id})
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                f"{self.BASE_URL}/PositionBook",
                data={"jData": payload, "jKey": self.session_token or ""},
            )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            raise RuntimeError("Shoonya position request failed")
        if not isinstance(data, list):
            raise ValueError("Shoonya returned an invalid positions response")
        return data

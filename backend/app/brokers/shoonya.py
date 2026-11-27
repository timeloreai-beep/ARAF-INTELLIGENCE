from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import math
import random
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx
import pyotp
from httpx import AsyncBaseTransport

from ..config import get_settings
from ..integrations.redis import RedisCache
from ..security import decrypt_broker_session, encrypt_broker_session
from .base import BrokerAdapter, BrokerOrderResult, BrokerQuote

logger = logging.getLogger(__name__)


class ReconnectState(Enum):
    IDLE = "idle"
    BACKING_OFF = "backing_off"
    RECONNECTING = "reconnecting"
    CONFLICT_DETECTED = "conflict_detected"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"


class ShoonyaAdapter(BrokerAdapter):
    BASE_URL = "https://api.shoonya.com/NorenWClientTP"
    CANDLE_INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
    OPTION_MASTER_URLS = {
        "NFO": "https://api.shoonya.com/NFO_symbols.txt.zip",
        "BFO": "https://api.shoonya.com/BFO_symbols.txt.zip",
    }
    _option_master_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
    _option_master_lock = asyncio.Lock()

    MAX_RECONNECT_ATTEMPTS = 5
    BASE_BACKOFF_SECONDS = 30
    MAX_BACKOFF_SECONDS = 300
    CONFLICT_DETECTION_TERMS = (
        "session",
        "susertoken",
        "jkey",
        "login",
        "invalid",
        "expired",
        "already logged",
        "another session",
        "force logout",
    )

    def __init__(
        self,
        transport: AsyncBaseTransport | None = None,
        redis: RedisCache | None = None,
    ) -> None:
        settings = get_settings()
        self.relay_url = settings.shoonya_relay_url
        self.api_key = settings.shoonya_api_key
        self.user_id = settings.shoonya_user_id
        self.password = settings.shoonya_password
        self.totp_secret = settings.shoonya_totp_secret
        self.vendor_code = settings.shoonya_vendor_code
        self.session_token: str | None = None
        self.transport = transport
        self.redis = redis
        self._reconnect_state = ReconnectState.IDLE
        self._reconnect_attempt = 0
        self._last_conflict_time: float | None = None
        self._login_lock = asyncio.Lock()
        self._session_invalidated = False

    def _totp(self) -> str:
        if not self.totp_secret:
            raise RuntimeError("SHOONYA_TOTP_SECRET is required; TOTP secret is never hard-coded")
        return pyotp.TOTP(self.totp_secret).now()

    @property
    def reconnect_state(self) -> ReconnectState:
        return self._reconnect_state

    @property
    def reconnect_attempt(self) -> int:
        return self._reconnect_attempt

    @property
    def is_session_invalidated(self) -> bool:
        return self._session_invalidated

    def _calculate_backoff(self) -> float:
        attempt = self._reconnect_attempt
        base = self.BASE_BACKOFF_SECONDS
        max_backoff = self.MAX_BACKOFF_SECONDS
        exponential = min(base * (2 ** attempt), max_backoff)
        jitter = random.uniform(0, exponential * 0.1)
        return exponential + jitter

    def _reset_reconnect_state(self) -> None:
        self._reconnect_state = ReconnectState.IDLE
        self._reconnect_attempt = 0
        self._session_invalidated = False

    def _set_conflict_detected(self) -> None:
        self._reconnect_state = ReconnectState.CONFLICT_DETECTED
        self._last_conflict_time = time.monotonic()
        self._session_invalidated = True
        logger.warning("Shoonya session conflict detected - external login invalidated session")

    def _enter_backoff(self) -> float:
        self._reconnect_state = ReconnectState.BACKING_OFF
        backoff = self._calculate_backoff()
        logger.info("Shoonya entering backoff for %.1fs (attempt %d/%d)", backoff, self._reconnect_attempt + 1, self.MAX_RECONNECT_ATTEMPTS)
        return backoff

    def _enter_reconnecting(self) -> None:
        self._reconnect_state = ReconnectState.RECONNECTING

    def _increment_reconnect_attempt(self) -> bool:
        self._reconnect_attempt += 1
        if self._reconnect_attempt >= self.MAX_RECONNECT_ATTEMPTS:
            self._reconnect_state = ReconnectState.MAX_RETRIES_EXCEEDED
            logger.error("Shoonya max reconnect attempts (%d) exceeded", self.MAX_RECONNECT_ATTEMPTS)
            return False
        return True

    async def login(self, *, force: bool = False) -> dict[str, Any]:
        token_key = f"broker:shoonya:session:{self.user_id or 'unconfigured'}"
        lock_key = f"{token_key}:login-lock"
        lock_token = uuid.uuid4().hex
        owns_lock = False
        if self.session_token and not force:
            self._reset_reconnect_state()
            return {"stat": "Ok", "susertoken": self.session_token, "cached": True}
        if self.redis and self.redis.enabled and not force:
            cached_token = await self.redis.get(token_key)
            if cached_token:
                try:
                    self.session_token = decrypt_broker_session(cached_token)
                    self._reset_reconnect_state()
                    return {"stat": "Ok", "susertoken": self.session_token, "cached": True}
                except ValueError:
                    await self.redis.delete(token_key)
        if self.redis and self.redis.enabled:
            owns_lock = await self.redis.set_if_absent(lock_key, lock_token, 30)
            if not owns_lock:
                for _ in range(50):
                    await asyncio.sleep(0.1)
                    cached_token = await self.redis.get(token_key)
                    if cached_token:
                        try:
                            self.session_token = decrypt_broker_session(cached_token)
                            self._reset_reconnect_state()
                            return {"stat": "Ok", "susertoken": self.session_token, "cached": True}
                        except ValueError:
                            continue
                raise RuntimeError("Shoonya login is already in progress")

        try:
            return await self._authenticate_and_cache(token_key)
        finally:
            if owns_lock and self.redis:
                await self.redis.compare_and_delete(lock_key, lock_token)

    async def _authenticate_and_cache(self, token_key: str) -> dict[str, Any]:
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
        async with httpx.AsyncClient(timeout=12, transport=self.transport) as client:
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
            error_msg = str(data.get("emsg", "")).lower()
            if any(term in error_msg for term in self.CONFLICT_DETECTION_TERMS):
                self._set_conflict_detected()
            raise RuntimeError("Shoonya authentication failed; check broker credentials and TOTP")
        self.session_token = data["susertoken"]
        self._reset_reconnect_state()
        if self.redis and self.redis.enabled:
            await self.redis.set(
                token_key,
                encrypt_broker_session(self.session_token),
                18 * 60 * 60,
            )
        return data

    async def smart_reconnect(self) -> bool:
        if not self._session_invalidated and self._reconnect_state == ReconnectState.IDLE:
            return True

        async with self._login_lock:
            if self.session_token and not self._session_invalidated:
                return True

            while self._reconnect_attempt < self.MAX_RECONNECT_ATTEMPTS:
                self._enter_reconnecting()
                try:
                    token_key = f"broker:shoonya:session:{self.user_id or 'unconfigured'}"
                    result = await self._authenticate_and_cache(token_key)
                    if self.session_token:
                        logger.info("Shoonya smart reconnect successful")
                        return True
                except RuntimeError as exc:
                    error_msg = str(exc).lower()
                    if any(term in error_msg for term in self.CONFLICT_DETECTION_TERMS):
                        self._set_conflict_detected()
                    if not self._increment_reconnect_attempt():
                        break
                    backoff = self._enter_backoff()
                    logger.warning("Shoonya reconnect attempt %d failed: %s", self._reconnect_attempt, exc)
                    await asyncio.sleep(backoff)
                    continue
                except (httpx.HTTPError, OSError) as exc:
                    if not self._increment_reconnect_attempt():
                        break
                    backoff = self._enter_backoff()
                    logger.warning("Shoonya reconnect attempt %d failed (network): %s", self._reconnect_attempt, exc)
                    await asyncio.sleep(backoff)
                    continue

            return False

    async def get_quote(self, symbol: str, exchange: str) -> BrokerQuote | None:
        if not self.session_token or self._session_invalidated:
            if self._session_invalidated:
                await self.smart_reconnect()
            else:
                await self.login()
        payload = json.dumps({"uid": self.user_id, "exch": exchange, "token": symbol})
        data = await self._session_post("GetQuotes", payload)
        if not isinstance(data, dict):
            raise ValueError("Shoonya returned an invalid quote response")
        price = float(data.get("lp", 0) or 0)
        if data.get("stat") != "Ok" or not math.isfinite(price) or price <= 0:
            return None
        high = float(data.get("h", price) or price)
        low = float(data.get("l", price) or price)
        volume = float(data.get("vol", 0) or 0)
        bid = float(data.get("bp1", 0) or 0)
        ask = float(data.get("sp1", 0) or 0)
        if not all(math.isfinite(value) for value in (high, low, volume, bid, ask)):
            raise ValueError("Shoonya returned invalid quote values")
        return BrokerQuote(
            symbol=symbol,
            exchange=exchange,
            price=price,
            high=high,
            low=low,
            volume=volume,
            source="SHOONYA",
            bid=bid if bid > 0 else None,
            ask=ask if ask > 0 else None,
        )

    async def get_option_master(
        self,
        exchange: str,
        *,
        refresh: bool = False,
    ) -> list[dict[str, Any]]:
        url = self.OPTION_MASTER_URLS.get(exchange)
        if not url:
            raise ValueError("Shoonya option master supports NFO or BFO")
        cached = self._option_master_cache.get(exchange)
        if not refresh and cached and time.monotonic() - cached[0] < 900:
            return [dict(row) for row in cached[1]]

        async with self._option_master_lock:
            cached = self._option_master_cache.get(exchange)
            if not refresh and cached and time.monotonic() - cached[0] < 900:
                return [dict(row) for row in cached[1]]
            async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                response = await client.get(url)
            response.raise_for_status()
            payload = response.content
            if not payload or len(payload) > 25 * 1024 * 1024:
                raise RuntimeError("Shoonya option master has an invalid size")
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    entries = [
                        info for info in archive.infolist()
                        if info.filename.lower().endswith(".txt")
                    ]
                    if len(entries) != 1 or entries[0].file_size > 100 * 1024 * 1024:
                        raise RuntimeError("Shoonya option master archive is invalid")
                    contents = archive.read(entries[0]).decode("utf-8-sig")
            except (zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
                raise RuntimeError("Shoonya option master could not be decoded") from exc
            reader = csv.DictReader(io.StringIO(contents))
            headers = {str(header).strip().lower() for header in reader.fieldnames or []}
            has_strike = "strikeprice" in headers or "strike" in headers
            if not {
                "exchange", "token", "lotsize", "symbol", "tradingsymbol",
                "expiry", "instrument", "optiontype",
            } <= headers or not has_strike:
                raise RuntimeError("Shoonya option master is missing required columns")
            rows = list(reader)
            if not rows:
                raise RuntimeError("Shoonya option master is empty")
            self._option_master_cache[exchange] = (time.monotonic(), rows)
            return [dict(row) for row in rows]

    async def search_instrument(self, symbol: str, exchange: str) -> dict[str, Any]:
        if not self.session_token or self._session_invalidated:
            if self._session_invalidated:
                await self.smart_reconnect()
            else:
                await self.login()
        payload = json.dumps({
            "uid": self.user_id,
            "exch": exchange,
            "stext": symbol,
        })
        data = await self._session_post("SearchScrips", payload)
        rows = data.get("values", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("Shoonya returned invalid instrument search data")
        requested = symbol.strip().upper()
        matches = [
            row for row in rows
            if isinstance(row, dict)
            and str(row.get("exch", exchange)).upper() == exchange.upper()
            and str(row.get("tsym", "")).strip().upper().split("-")[0] == requested.split("-")[0]
            and row.get("token")
        ]
        if len(matches) != 1:
            raise RuntimeError("Shoonya instrument lookup was missing or ambiguous")
        return matches[0]

    async def get_historical_candles(
        self,
        exchange: str,
        token: str,
        *,
        interval: str = "5m",
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        if interval not in self.CANDLE_INTERVALS:
            raise ValueError("Unsupported Shoonya candle interval")
        if not token.isdigit():
            raise ValueError("Shoonya instrument token must be numeric")
        if not self.session_token or self._session_invalidated:
            if self._session_invalidated:
                await self.smart_reconnect()
            else:
                await self.login()
        interval_minutes = self.CANDLE_INTERVALS[interval]
        now = datetime.now(timezone.utc)
        payload = json.dumps({
            "uid": self.user_id,
            "exch": exchange,
            "token": token,
            "st": str(int((now - timedelta(minutes=interval_minutes * limit * 2)).timestamp())),
            "et": str(int(now.timestamp())),
            "intrv": str(interval_minutes),
        })
        data = await self._session_post("TPSeries", payload, timeout=15)
        if isinstance(data, dict) or not isinstance(data, list):
            raise RuntimeError("Shoonya historical candle request failed")

        india_tz = timezone(timedelta(hours=5, minutes=30))
        now_ms = int(now.timestamp() * 1000)
        duration_ms = interval_minutes * 60 * 1000
        candles: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            timestamp = row.get("time") or row.get("ssboe")
            try:
                if isinstance(timestamp, (int, float)) or str(timestamp).isdigit():
                    opened = datetime.fromtimestamp(float(timestamp), timezone.utc)
                else:
                    opened = None
                    for date_format in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                        try:
                            opened = datetime.strptime(str(timestamp), date_format).replace(tzinfo=india_tz)
                            break
                        except ValueError:
                            continue
                    if opened is None:
                        opened = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                        if opened.tzinfo is None:
                            opened = opened.replace(tzinfo=india_tz)
                opened_ms = int(opened.astimezone(timezone.utc).timestamp() * 1000)
                open_price, high, low, close, volume = (
                    float(row[key]) for key in ("into", "inth", "intl", "intc", "intv")
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            values = (open_price, high, low, close, volume)
            if (
                opened_ms + duration_ms > now_ms
                or not all(math.isfinite(value) for value in values)
                or min(open_price, high, low, close) <= 0
                or volume < 0
                or low > min(open_price, close)
                or high < max(open_price, close)
            ):
                continue
            candles.append({
                "time": opened_ms,
                "close_time": opened_ms + duration_ms,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "is_closed": True,
            })
        candles.sort(key=lambda candle: candle["time"])
        return candles[-min(max(limit, 16), 500):]

    async def place_order(self, **kwargs: Any) -> BrokerOrderResult:
        if not self.session_token or self._session_invalidated:
            if self._session_invalidated:
                await self.smart_reconnect()
            else:
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
        data = await self._session_post("PlaceOrder", json.dumps(jdata))
        accepted = data.get("stat") == "Ok"
        return BrokerOrderResult(
            accepted=accepted,
            broker_order_id=data.get("norenordno"),
            status="ACCEPTED" if accepted else "REJECTED",
            message=data.get("emsg", ""),
            raw=data,
        )

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self.session_token or self._session_invalidated:
            if self._session_invalidated:
                await self.smart_reconnect()
            else:
                await self.login()
        payload = json.dumps({"uid": self.user_id, "actid": self.user_id})
        data = await self._session_post("PositionBook", payload)
        if isinstance(data, dict):
            raise RuntimeError("Shoonya position request failed")
        if not isinstance(data, list):
            raise ValueError("Shoonya returned an invalid positions response")
        return data

    async def get_balance(self) -> dict[str, Any]:
        """Get Shoonya account balance/limits."""
        if not self.session_token or self._session_invalidated:
            if self._session_invalidated:
                await self.smart_reconnect()
            else:
                await self.login()
        payload = json.dumps({"uid": self.user_id, "actid": self.user_id})
        data = await self._session_post("Limits", payload)
        if isinstance(data, dict) and data.get("stat") != "Ok":
            raise RuntimeError(f"Shoonya limits request failed: {data.get('emsg', 'Unknown error')}")
        if not isinstance(data, list):
            raise ValueError("Shoonya returned an invalid limits response")
        # Shoonya returns a list with one element containing balance info
        balance_data = data[0] if data else {}
        return {
            "cash": float(balance_data.get("cash", 0) or 0),
            "margin": float(balance_data.get("margin", 0) or 0),
            "available_margin": float(balance_data.get("avlbl_margin", balance_data.get("available_margin", 0)) or 0),
            "used_margin": float(balance_data.get("used_margin", 0) or 0),
            "span_margin": float(balance_data.get("span_margin", 0) or 0),
            "exposure_margin": float(balance_data.get("exposure_margin", 0) or 0),
            "linked": True,
            "status": "LIVE",
        }

    async def _session_post(
        self,
        endpoint: str,
        payload: str,
        *,
        timeout: float = 12,
    ) -> Any:
        for attempt in range(3):
            if not self.session_token:
                await self.login()
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.BASE_URL}/{endpoint}",
                    data={"jData": payload, "jKey": self.session_token or ""},
                )
            response.raise_for_status()
            data = response.json()
            message = str(data.get("emsg", "")).lower() if isinstance(data, dict) else ""
            if (
                isinstance(data, dict)
                and data.get("stat") != "Ok"
                and any(term in message for term in self.CONFLICT_DETECTION_TERMS)
            ):
                logger.warning("Shoonya session conflict detected on %s: %s", endpoint, message)
                self._set_conflict_detected()
                if await self.smart_reconnect():
                    continue
                raise RuntimeError("Shoonya smart reconnect failed after max attempts")
            return data
        raise RuntimeError("Shoonya session refresh failed after retries")

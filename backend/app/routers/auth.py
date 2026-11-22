import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, TypeVar

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..config import get_settings
from ..dependencies import current_subject
from ..schemas import (
    KycSubmission,
    LoginRequest,
    OtpSendRequest,
    OtpVerifyRequest,
    PinRequest,
    TokenResponse,
    TraderUidLoginRequest,
)
from ..security import (
    create_pbkdf2_pin,
    create_pin_session_token,
    decode_access_token,
    verify_pbkdf2_pin,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")
router = APIRouter(prefix="/auth", tags=["auth"])
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OTP_TTL_SECONDS = 600
OTP_COOLDOWN_SECONDS = 60
OTP_MAX_ATTEMPTS = 5
OTP_VERIFIED_TTL = timedelta(minutes=30)
PIN_MAX_ATTEMPTS = 5
PIN_LOCK_SECONDS = 900


def _require_database(request: Request) -> Any:
    database = request.app.state.database
    if not database.enabled:
        raise HTTPException(status_code=503, detail="Authentication database unavailable")
    return database


def _require_redis(request: Request) -> Any:
    redis = request.app.state.redis
    if not redis.enabled:
        raise HTTPException(status_code=503, detail="Authentication rate-limit storage unavailable")
    return redis


def _has_persistence(status: Any) -> bool:
    return bool(status.primary_applied or status.backup_applied)


async def _db_call(action: Awaitable[T], failure_detail: str) -> T:
    try:
        return await action
    except (SQLAlchemyError, RuntimeError, OSError, TimeoutError) as exc:
        logger.warning("%s: %s", failure_detail, type(exc).__name__)
        raise HTTPException(status_code=503, detail=failure_detail) from exc


def _otp_digest(email: str, purpose: str, code: str) -> str:
    settings = get_settings()
    secret = settings.app_secret or settings.jwt_secret
    if not secret:
        raise HTTPException(status_code=503, detail="OTP signing secret is not configured")
    value = f"{email.lower()}:{purpose}:{code}".encode()
    return hmac.new(secret.encode(), value, hashlib.sha256).hexdigest()


def _firebase_api_url() -> str:
    api_key = get_settings().firebase_web_api_key
    if not api_key:
        raise HTTPException(status_code=503, detail="Firebase identity API is not configured")
    return f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"


async def _firebase_password_login(email: str, password: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                _firebase_api_url(),
                json={"email": email, "password": password, "returnSecureToken": True},
            )
    except httpx.HTTPError as exc:
        logger.warning("Firebase password login transport failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Identity provider unavailable") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid identity provider response") from exc
    if response.status_code in (400, 401):
        error = data.get("error") if isinstance(data, dict) else None
        code = str(error.get("message", "")) if isinstance(error, dict) else ""
        if code in {
            "EMAIL_NOT_FOUND",
            "INVALID_PASSWORD",
            "INVALID_LOGIN_CREDENTIALS",
            "INVALID_EMAIL",
        }:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if code == "USER_DISABLED":
            raise HTTPException(status_code=403, detail="Account is disabled")
        if code == "TOO_MANY_ATTEMPTS_TRY_LATER":
            raise HTTPException(status_code=429, detail="Too many login attempts")
        raise HTTPException(status_code=502, detail="Identity provider request failed")
    if response.status_code >= 500 or response.status_code >= 300:
        raise HTTPException(status_code=502, detail="Identity provider request failed")
    if not isinstance(data, dict) or not data.get("idToken"):
        raise HTTPException(status_code=502, detail="Invalid identity provider response")
    return data


def _encrypt_kyc_pii(values: dict[str, str]) -> str:
    secret = get_settings().kyc_pii_encryption_key
    if not secret:
        raise HTTPException(status_code=503, detail="KYC encryption is not configured")
    key = hashlib.sha256(secret.encode()).digest()
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(
        nonce,
        json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode(),
        b"araf-kyc-v1",
    )
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def _verified_pin_session(token: str | None, subject: str) -> bool:
    if not token:
        return False
    claims = decode_access_token(token)
    return bool(
        claims
        and claims.get("sub") == subject
        and claims.get("scope") == "pin_session"
    )


async def _otp_is_recently_verified(database: Any, email: str, purpose: str) -> bool:
    challenge = await _db_call(
        database.get_auth_otp(email, purpose), "OTP storage unavailable"
    )
    return bool(
        challenge
        and challenge.verified_at
        and not challenge.consumed_at
        and challenge.verified_at >= datetime.now(timezone.utc) - OTP_VERIFIED_TTL
    )


async def _ensure_user(database: Any, request: Request, subject: str) -> Any:
    user = await _db_call(
        database.get_app_user(subject), "User profile storage unavailable"
    )
    if user:
        return user
    claims = request.state.auth_claims
    status = await _db_call(
        database.upsert_app_user(
            external_subject=subject,
            provider="firebase",
            email=claims.get("email"),
            display_name=claims.get("name"),
            role="admin" if claims.get("role") == "admin" or claims.get("admin") is True else "user",
            is_active=True,
        ),
        "Could not persist user profile",
    )
    if not _has_persistence(status):
        raise HTTPException(status_code=503, detail="Could not persist user profile")
    return await _db_call(
        database.get_app_user(subject), "User profile storage unavailable"
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    email = payload.identifier.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=400, detail="Use an email address to sign in")
    data = await _firebase_password_login(email, payload.password)
    return TokenResponse(access_token=str(data["idToken"]))


@router.post("/resolve-trader-uid")
async def resolve_trader_uid(payload: TraderUidLoginRequest, request: Request) -> dict[str, Any]:
    database = _require_database(request)
    redis = _require_redis(request)
    client_ip = request.client.host if request.client else "unknown"
    try:
        if await redis.increment_with_expiry(f"auth:uid-login:ip:{client_ip}", 60) > 15:
            raise HTTPException(status_code=429, detail="Too many login attempts")
    except RedisError as exc:
        logger.warning("Trader UID login rate limiting unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Login rate limiting unavailable") from exc
    user = await _db_call(
        database.get_app_user_by_trader_uid(payload.trader_uid.strip().upper()),
        "User profile storage unavailable",
    )
    if not user or not user.email or not user.is_active:
        raise HTTPException(status_code=404, detail="Trader UID was not found")
    await _firebase_password_login(user.email, payload.password)
    return {"success": True, "email": user.email}


@router.post("/otp/send")
async def send_server_otp(
    payload: OtpSendRequest,
    request: Request,
) -> dict[str, Any]:
    email = payload.email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    database = _require_database(request)
    redis = _require_redis(request)
    client_ip = request.client.host if request.client else "unknown"
    try:
        rate = await redis.increment_with_expiry(f"auth:otp:ip:{client_ip}", 60)
        if rate > 10:
            raise HTTPException(status_code=429, detail="Too many OTP requests")
        allowed = await redis.set_if_absent(
            f"auth:otp:cooldown:{email}:{payload.purpose}", "1", OTP_COOLDOWN_SECONDS
        )
        if not allowed:
            raise HTTPException(status_code=429, detail="Please wait before requesting another OTP")
    except RedisError as exc:
        logger.warning("OTP rate limiting unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="OTP rate limiting unavailable") from exc

    settings = get_settings()
    if not settings.gmail_bridge_url or not settings.gmail_bridge_secret:
        await redis.delete(f"auth:otp:cooldown:{email}:{payload.purpose}")
        raise HTTPException(status_code=503, detail="OTP delivery is not configured")

    code = str(secrets.randbelow(900_000) + 100_000)
    challenge = await _db_call(
        database.get_auth_otp(email, payload.purpose), "OTP storage unavailable"
    )
    status = await database.upsert_auth_otp(
        id=challenge.id if challenge else str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"auth-otp:{email}:{payload.purpose}"
        )),
        email=email,
        purpose=payload.purpose,
        code_hash=_otp_digest(email, payload.purpose, code),
        attempts=0,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS),
        verified_at=None,
        consumed_at=None,
    )
    if not _has_persistence(status):
        await redis.delete(f"auth:otp:cooldown:{email}:{payload.purpose}")
        raise HTTPException(status_code=503, detail="Could not persist OTP challenge")

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                settings.gmail_bridge_url,
                json={"secret": settings.gmail_bridge_secret, "to": email, "otp": code},
            )
    except httpx.HTTPError as exc:
        await database.delete_auth_otp(email, payload.purpose)
        await redis.delete(f"auth:otp:cooldown:{email}:{payload.purpose}")
        logger.warning("OTP delivery failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="OTP delivery failed") from exc
    try:
        delivery_result = response.json()
    except ValueError:
        delivery_result = None
    if (
        not response.is_success
        or not isinstance(delivery_result, dict)
        or delivery_result.get("success") is not True
    ):
        await database.delete_auth_otp(email, payload.purpose)
        await redis.delete(f"auth:otp:cooldown:{email}:{payload.purpose}")
        raise HTTPException(status_code=502, detail="OTP delivery failed")
    return {"success": True, "message": "Verification code sent"}


@router.post("/otp/verify")
async def verify_server_otp(
    payload: OtpVerifyRequest,
    request: Request,
) -> dict[str, Any]:
    email = payload.email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    database = _require_database(request)
    redis = _require_redis(request)
    challenge = await _db_call(
        database.get_auth_otp(email, payload.purpose), "OTP storage unavailable"
    )
    now = datetime.now(timezone.utc)
    if not challenge:
        raise HTTPException(status_code=404, detail="OTP challenge not found")
    if challenge.expires_at <= now or challenge.verified_at or challenge.consumed_at:
        raise HTTPException(status_code=400, detail="OTP is invalid or expired")
    attempts_key = f"auth:otp:attempts:{email}:{payload.purpose}"
    try:
        attempts = await redis.increment_with_expiry(attempts_key, OTP_TTL_SECONDS)
    except RedisError as exc:
        logger.warning("OTP attempt counter unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="OTP verification temporarily unavailable") from exc
    if attempts > OTP_MAX_ATTEMPTS:
        await database.delete_auth_otp(email, payload.purpose)
        raise HTTPException(status_code=429, detail="OTP attempt limit reached")
    expected = _otp_digest(email, payload.purpose, payload.code)
    if not hmac.compare_digest(challenge.code_hash, expected):
        await database.upsert_auth_otp(
            id=challenge.id,
            email=email,
            purpose=payload.purpose,
            code_hash=challenge.code_hash,
            attempts=attempts,
            expires_at=challenge.expires_at,
            verified_at=None,
            consumed_at=None,
        )
        raise HTTPException(status_code=401, detail="OTP is incorrect")
    status = await database.upsert_auth_otp(
        id=challenge.id,
        email=email,
        purpose=payload.purpose,
        code_hash=challenge.code_hash,
        attempts=attempts,
        expires_at=challenge.expires_at,
        verified_at=now,
        consumed_at=None,
    )
    if not _has_persistence(status):
        raise HTTPException(status_code=503, detail="Could not persist OTP verification")
    await redis.delete(attempts_key)
    return {"success": True, "message": "OTP verified"}


@router.post("/kyc")
async def submit_kyc(
    payload: KycSubmission,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    database = _require_database(request)
    claims = request.state.auth_claims
    email = str(claims.get("email") or payload.email or "").strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise HTTPException(status_code=400, detail="Verified account email is required")
    otp_signup_verified = not bool(claims.get("email_verified")) and await _otp_is_recently_verified(
        database, email, "SIGNUP"
    )
    if not claims.get("email_verified") and not otp_signup_verified:
        raise HTTPException(status_code=403, detail="Verify your email before KYC submission")

    existing = await _db_call(
        database.get_kyc_record(subject), "KYC storage unavailable"
    )
    if existing:
        return {
            "success": True,
            "traderUid": existing.trader_uid,
            "kycStatus": existing.status,
        }

    user = await _ensure_user(database, request, subject)
    trader_uid = user.trader_uid
    if not trader_uid:
        for _ in range(5):
            candidate = f"AR{secrets.token_hex(4).upper()}"
            if not await _db_call(
                database.get_app_user_by_trader_uid(candidate),
                "User profile storage unavailable",
            ):
                trader_uid = candidate
                break
        if not trader_uid:
            raise HTTPException(status_code=503, detail="Could not allocate Trader UID")

    full_name = payload.full_name.strip()
    pan = payload.pan.upper()
    aadhaar = payload.aadhaar_last4
    pii = _encrypt_kyc_pii(
        {
            "email": email,
            "phone": payload.phone,
            "pan": pan,
            "aadhaar_last4": aadhaar,
            "dob": payload.dob or "",
        }
    )
    trial_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    kyc_status = await database.upsert_kyc_record(
        user_id=subject,
        trader_uid=trader_uid,
        full_name=full_name,
        pan_masked=f"{pan[:2]}******{pan[-2:]}",
        aadhaar_masked=f"XXXX-XXXX-{aadhaar}",
        income_bracket=payload.income_bracket or "-",
        experience=payload.experience or "-",
        authorized_segment=payload.authorized_segment,
        status="PENDING_ADMIN_REVIEW",
        encrypted_pii=pii,
        trial_expires_at=trial_expires_at,
        submitted_at=datetime.now(timezone.utc),
    )
    if not _has_persistence(kyc_status):
        raise HTTPException(status_code=503, detail="Could not persist KYC application")
    user_status = await _db_call(
        database.upsert_app_user(
            external_subject=subject,
            provider="firebase",
            email=email,
            display_name=full_name,
            trader_uid=trader_uid,
            role=user.role if user else "user",
            kyc_status="PENDING_ADMIN_REVIEW",
            is_active=True,
        ),
        "Could not persist KYC profile",
    )
    if not _has_persistence(user_status):
        raise HTTPException(status_code=503, detail="Could not persist KYC profile")
    if otp_signup_verified:
        consumed = await _db_call(
            database.consume_auth_otp(email, "SIGNUP"), "Could not consume signup OTP"
        )
        if not _has_persistence(consumed):
            raise HTTPException(status_code=503, detail="Could not consume signup OTP")
    return {
        "success": True,
        "traderUid": trader_uid,
        "kycStatus": "PENDING_ADMIN_REVIEW",
    }


@router.post("/pin/verify")
async def verify_pin(
    payload: PinRequest,
    request: Request,
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    database = _require_database(request)
    redis = _require_redis(request)
    credential = await _db_call(
        database.get_pin_credential(subject), "PIN storage unavailable"
    )
    if not credential:
        raise HTTPException(status_code=404, detail="PIN is not configured")
    attempts_key = f"auth:pin:attempts:{subject}"
    try:
        attempts_value = await redis.get(attempts_key)
        if attempts_value and int(attempts_value) >= PIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="PIN is locked; try again later")
    except (RedisError, ValueError) as exc:
        logger.warning("PIN attempt counter unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="PIN verification temporarily unavailable") from exc
    if not verify_pbkdf2_pin(payload.pin, credential.salt_hex, credential.pin_hash):
        try:
            attempts = await redis.increment_with_expiry(attempts_key, PIN_LOCK_SECONDS)
        except RedisError as exc:
            logger.warning("PIN attempt counter unavailable: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="PIN verification temporarily unavailable") from exc
        if attempts >= PIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="PIN attempt limit reached")
        raise HTTPException(status_code=401, detail="PIN is incorrect")
    await redis.delete(attempts_key)
    try:
        pin_token = create_pin_session_token(subject)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True, "pinToken": pin_token}


@router.post("/pin")
async def set_master_pin(
    payload: PinRequest,
    request: Request,
    x_pin_token: str | None = Header(default=None, alias="X-Pin-Token"),
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    database = _require_database(request)
    redis = _require_redis(request)
    credential = await _db_call(
        database.get_pin_credential(subject), "PIN storage unavailable"
    )
    reset_verified = False
    if credential:
        claims = request.state.auth_claims
        email = str(claims.get("email") or "").lower()
        reset_verified = bool(
            email and await _otp_is_recently_verified(database, email, "PIN_RESET")
        )
        if not _verified_pin_session(x_pin_token, subject) and not reset_verified:
            raise HTTPException(status_code=403, detail="An active PIN session or verified reset OTP is required")

    await _ensure_user(database, request, subject)
    hashed = create_pbkdf2_pin(payload.pin)
    status = await database.upsert_pin_credential(user_id=subject, **hashed)
    if not _has_persistence(status):
        raise HTTPException(status_code=503, detail="Could not persist PIN")
    await redis.delete(f"auth:pin:attempts:{subject}")
    claims = request.state.auth_claims
    email = str(claims.get("email") or "").lower()
    if email and reset_verified:
        consumed = await _db_call(
            database.consume_auth_otp(email, "PIN_RESET"),
            "Could not consume PIN reset OTP",
        )
        if not _has_persistence(consumed):
            raise HTTPException(status_code=503, detail="Could not consume PIN reset OTP")
    try:
        pin_token = create_pin_session_token(subject)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True, "pinToken": pin_token}


@router.get("/master-user")
async def get_master_user(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    database = _require_database(request)
    user = await _db_call(
        database.get_app_user(subject), "User profile storage unavailable"
    )
    if not user:
        raise HTTPException(status_code=404, detail="User profile not found")
    kyc = await _db_call(
        database.get_kyc_record(subject), "KYC storage unavailable"
    )
    prefs = await _db_call(
        database.get_user_preferences(subject), "Preference storage unavailable"
    )
    profile = {
        "email": user.email,
        "fullName": kyc.full_name if kyc else user.display_name,
        "traderUid": user.trader_uid,
        "kycStatus": user.kyc_status,
        "panMasked": kyc.pan_masked if kyc else None,
        "aadhaarMasked": kyc.aadhaar_masked if kyc else None,
        "authorizedSegment": kyc.authorized_segment if kyc else None,
        "trialExpiresAt": kyc.trial_expires_at.isoformat() if kyc else None,
        "isVipFree": False,
    }
    return {
        "success": True,
        "hasPin": await _db_call(
            database.get_pin_credential(subject), "PIN storage unavailable"
        ) is not None,
        "fullData": {"profile": profile, "preferences": prefs.preferences if prefs else {}},
    }


@router.post("/master-user")
async def save_master_user(
    payload: dict[str, Any],
    request: Request,
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    database = _require_database(request)
    full_data = payload.get("fullData", payload)
    if not isinstance(full_data, dict):
        raise HTTPException(status_code=422, detail="fullData must be an object")
    preferences = full_data.get("preferences", {})
    if not isinstance(preferences, dict):
        raise HTTPException(status_code=422, detail="preferences must be an object")
    allowed = {"voiceLang", "newsLang", "aiChatLang", "customRelayUrl"}
    safe_preferences = {
        key: str(value)[:255]
        for key, value in preferences.items()
        if key in allowed and isinstance(value, (str, int, float, bool))
    }
    status = await _db_call(
        database.upsert_user_preferences(subject, safe_preferences),
        "Could not persist user preferences",
    )
    if not _has_persistence(status):
        raise HTTPException(status_code=503, detail="Could not persist user preferences")
    return {"success": True}


@router.post("/logout")
async def logout_session(
    request: Request,
    authorization: str | None = Header(default=None),
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    redis = request.app.state.redis
    if not redis.enabled:
        raise HTTPException(status_code=503, detail="Session revocation storage unavailable")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.removeprefix("Bearer ").strip()
    expires_at = int(request.state.auth_claims.get("exp", 0))
    ttl = max(1, expires_at - int(time.time()))
    token_key = hashlib.sha256(token.encode()).hexdigest()
    try:
        await redis.set(f"auth:revoked-token:{token_key}", "1", ttl_seconds=ttl)
        await redis.delete(f"session:{subject}")
    except RedisError as exc:
        logger.warning("Could not revoke user session: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not revoke user session") from exc
    return {"success": True}


@router.post("/session")
async def sync_authenticated_session(
    request: Request,
    subject: str = Depends(current_subject),
) -> dict[str, Any]:
    claims = request.state.auth_claims
    database = request.app.state.database
    redis = request.app.state.redis
    role = "admin" if claims.get("role") == "admin" or claims.get("admin") is True else "user"
    persistence: dict[str, object] = {}

    if database.enabled:
        try:
            status = await database.upsert_app_user(
                external_subject=subject,
                provider="firebase",
                email=claims.get("email"),
                display_name=claims.get("name"),
                role=role,
                is_active=True,
            )
            persistence["postgres"] = status.as_dict()
        except (SQLAlchemyError, RuntimeError, OSError, TimeoutError):
            logger.exception("Could not persist authenticated user profile")
            persistence["postgres"] = {"health": "error"}
    else:
        persistence["postgres"] = {"health": "not_configured"}

    if redis.enabled:
        try:
            expires_at = int(claims.get("exp", 0))
            ttl = max(1, min(expires_at - int(time.time()), 3600))
            await redis.set_json(
                f"session:{subject}",
                {
                    "subject": subject,
                    "provider": "firebase",
                    "role": role,
                    "expires_at": expires_at,
                },
                ttl_seconds=ttl,
            )
            persistence["redis"] = "stored"
        except (RedisError, TypeError, ValueError):
            logger.exception("Could not cache authenticated session")
            persistence["redis"] = "error"
    else:
        persistence["redis"] = "not_configured"

    degraded = any(
        value.get("health") != "healthy"
        if isinstance(value, dict)
        else value in {"error", "not_configured"}
        for value in persistence.values()
    )
    return {
        "success": True,
        "subject": subject,
        "role": role,
        "persistence": persistence,
        "persistence_degraded": degraded,
    }

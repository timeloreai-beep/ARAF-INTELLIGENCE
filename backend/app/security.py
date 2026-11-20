from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_secret(value: str) -> str:
    return pwd_context.hash(value)


def verify_secret(value: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(value, hashed)
    except Exception:
        return False


def derive_pbkdf2_pin(pin: str, salt_hex: str, iterations: int = 100_000) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        pin.encode(),
        bytes.fromhex(salt_hex),
        iterations,
    ).hex()


def create_pbkdf2_pin(pin: str) -> dict[str, str]:
    salt_hex = secrets.token_hex(16)
    return {"salt_hex": salt_hex, "pin_hash": derive_pbkdf2_pin(pin, salt_hex)}


def verify_pbkdf2_pin(pin: str, salt_hex: str, pin_hash: str) -> bool:
    derived = derive_pbkdf2_pin(pin, salt_hex)
    return hmac.compare_digest(derived, pin_hash)


def create_access_token(subject: str, role: str = "trader", ttl_minutes: int | None = None) -> str:
    settings = get_settings()
    secret = settings.jwt_secret or settings.app_secret
    if not secret:
        raise RuntimeError("JWT_SECRET or APP_SECRET is required")
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=ttl_minutes or settings.access_token_ttl_minutes
    )
    payload: dict[str, Any] = {"sub": subject, "role": role, "exp": expires}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    secret = settings.jwt_secret or settings.app_secret
    if not secret:
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError:
        return None

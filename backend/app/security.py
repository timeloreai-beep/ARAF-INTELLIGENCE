from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import secrets
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _broker_session_key() -> bytes:
    settings = get_settings()
    secret = settings.app_secret or settings.jwt_secret
    if not secret:
        raise RuntimeError("APP_SECRET or JWT_SECRET is required to protect broker sessions")
    return hashlib.sha256(secret.encode()).digest()


def encrypt_broker_session(token: str) -> str:
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_broker_session_key()).encrypt(
        nonce, token.encode(), b"araf-shoonya-session-v1"
    )
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_broker_session(value: str) -> str:
    try:
        encrypted = base64.urlsafe_b64decode(value.encode("ascii"))
        nonce, ciphertext = encrypted[:12], encrypted[12:]
        if len(nonce) != 12 or not ciphertext:
            raise ValueError("Invalid encrypted broker session")
        return AESGCM(_broker_session_key()).decrypt(
            nonce, ciphertext, b"araf-shoonya-session-v1"
        ).decode()
    except (InvalidTag, ValueError, UnicodeError) as exc:
        raise ValueError("Invalid encrypted broker session") from exc


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


def create_ghost_admin_token(subject: str, ttl_minutes: int = 15) -> str:
    settings = get_settings()
    secret = settings.jwt_secret or settings.app_secret
    if not secret:
        raise RuntimeError("JWT_SECRET or APP_SECRET is required")
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "scope": "ghost_admin",
        "exp": expires,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def create_pin_session_token(subject: str) -> str:
    settings = get_settings()
    secret = settings.jwt_secret or settings.app_secret
    if not secret:
        raise RuntimeError("JWT_SECRET or APP_SECRET is required")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.pin_ttl_minutes)
    return jwt.encode(
        {"sub": subject, "scope": "pin_session", "exp": expires},
        secret,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    secret = settings.jwt_secret or settings.app_secret
    if not secret:
        return None
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError:
        return None

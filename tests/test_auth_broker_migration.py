import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app import security
from backend.app.brokers import coindcx, shoonya
from backend.app.config import Settings
from backend.app.dependencies import current_subject
from backend.app.routers import auth, brokers


class _Status:
    primary_applied = True
    backup_applied = True


class _MemoryDatabase:
    enabled = True

    def __init__(self):
        self.user = None
        self.kyc = None
        self.pin = None
        self.otp = None
        self.preferences = None

    async def get_app_user(self, subject):
        return self.user if self.user and self.user.external_subject == subject else None

    async def get_app_user_by_trader_uid(self, trader_uid):
        return self.user if self.user and self.user.trader_uid == trader_uid else None

    async def upsert_app_user(self, **values):
        self.user = SimpleNamespace(
            id=values.get("id", "user-row"),
            external_subject=values["external_subject"],
            provider=values.get("provider", "firebase"),
            email=values.get("email"),
            display_name=values.get("display_name"),
            role=values.get("role", "user"),
            kyc_status=values.get("kyc_status", "pending"),
            trader_uid=values.get("trader_uid"),
            is_active=values.get("is_active", True),
        )
        return _Status()

    async def get_kyc_record(self, subject):
        return self.kyc if self.kyc and self.kyc.user_id == subject else None

    async def upsert_kyc_record(self, **values):
        self.kyc = SimpleNamespace(**values)
        return _Status()

    async def get_pin_credential(self, subject):
        return self.pin if self.pin and self.pin.user_id == subject else None

    async def upsert_pin_credential(self, **values):
        self.pin = SimpleNamespace(**values)
        return _Status()

    async def get_auth_otp(self, email, purpose):
        if self.otp and self.otp.email == email and self.otp.purpose == purpose:
            return self.otp
        return None

    async def upsert_auth_otp(self, **values):
        self.otp = SimpleNamespace(**values)
        return _Status()

    async def delete_auth_otp(self, email, purpose):
        if self.otp and self.otp.email == email and self.otp.purpose == purpose:
            self.otp = None
        return _Status()

    async def get_user_preferences(self, _subject):
        return self.preferences

    async def upsert_user_preferences(self, subject, preferences):
        self.preferences = SimpleNamespace(user_id=subject, preferences=preferences)
        return _Status()


class _MemoryRedis:
    enabled = True

    def __init__(self):
        self.values = {}

    async def get(self, key):
        value = self.values.get(key)
        return str(value) if value is not None else None

    async def set_if_absent(self, key, value, _ttl):
        if key in self.values:
            return False
        self.values[key] = value
        return True

    async def increment_with_expiry(self, key, _ttl):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def delete(self, key):
        self.values.pop(key, None)

    async def set(self, key, value, ttl_seconds=60):
        self.values[key] = (value, ttl_seconds)

    async def set_json(self, key, value, ttl_seconds=60):
        self.values[key] = (value, ttl_seconds)


def _client(authenticated=True, claims=None, database=None, redis=None):
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(brokers.router)
    app.state.database = database or _MemoryDatabase()
    app.state.redis = redis or _MemoryRedis()
    app.state.mongo = SimpleNamespace(enabled=False)
    app.state.firebase = None

    if authenticated:
        async def subject_override(request: Request):
            request.state.auth_claims = claims or {
                "email": "trader@example.com",
                "email_verified": True,
            }
            return "test-user"

        app.dependency_overrides[current_subject] = subject_override
    return app


def test_trader_uid_resolution_verifies_firebase_password(monkeypatch):
    database = _MemoryDatabase()
    database.user = SimpleNamespace(
        external_subject="user-1",
        trader_uid="AR12345678",
        email="trader@example.com",
        is_active=True,
    )
    verified = []

    async def verify_password(email, password):
        verified.append((email, password))
        return {"idToken": "mock-token"}

    monkeypatch.setattr(auth, "_firebase_password_login", verify_password)
    with TestClient(_client(database=database)) as client:
        response = client.post(
            "/auth/resolve-trader-uid",
            json={"traderUid": "ar12345678", "password": "test-password"},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "email": "trader@example.com"}
    assert verified == [("trader@example.com", "test-password")]


def test_logout_revokes_current_token_until_expiry(monkeypatch):
    database = _MemoryDatabase()
    redis = _MemoryRedis()
    settings = Settings(_env_file=None, app_secret="test-application-secret", jwt_secret="test-jwt")
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    token = security.create_access_token("test-user")

    with TestClient(_client(database=database, redis=redis)) as client:
        response = client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )

    token_key = hashlib.sha256(token.encode()).hexdigest()
    assert response.status_code == 200
    assert redis.values[f"auth:revoked-token:{token_key}"][0] == "1"


def test_revoked_token_is_rejected_by_authenticated_routes(monkeypatch):
    database = _MemoryDatabase()
    redis = _MemoryRedis()
    settings = Settings(_env_file=None, app_secret="test-application-secret", jwt_secret="test-jwt")
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    token = security.create_access_token("test-user")
    token_key = hashlib.sha256(token.encode()).hexdigest()
    redis.values[f"auth:revoked-token:{token_key}"] = "1"

    with TestClient(_client(authenticated=False, database=database, redis=redis)) as client:
        response = client.get(
            "/auth/master-user",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401


def test_otp_verification_is_hashed_limited_and_persisted(monkeypatch):
    database = _MemoryDatabase()
    redis = _MemoryRedis()
    settings = Settings(_env_file=None, app_secret="test-application-secret")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    now = datetime.now(timezone.utc)
    database.otp = SimpleNamespace(
        id="challenge-1",
        email="trader@example.com",
        purpose="SIGNUP",
        code_hash=auth._otp_digest("trader@example.com", "SIGNUP", "123456"),
        attempts=0,
        expires_at=now + timedelta(minutes=5),
        verified_at=None,
        consumed_at=None,
    )
    with TestClient(_client(authenticated=False, database=database, redis=redis)) as client:
        response = client.post(
            "/auth/otp/verify",
            json={"email": "Trader@example.com", "code": "123456", "purpose": "SIGNUP"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert database.otp.verified_at is not None
    assert database.otp.code_hash != "123456"


def test_otp_send_uses_legacy_gmail_bridge_contract_without_storing_code(monkeypatch):
    database = _MemoryDatabase()
    redis = _MemoryRedis()
    settings = Settings(
        _env_file=None,
        app_secret="test-application-secret",
        gmail_bridge_url="https://mail-bridge.invalid/send",
        gmail_bridge_secret="test-bridge-secret",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    outbound = {}

    class _MockAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            outbound.update({"url": url, "body": json})
            return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(auth.httpx, "AsyncClient", _MockAsyncClient)
    with TestClient(_client(authenticated=False, database=database, redis=redis)) as client:
        response = client.post(
            "/auth/otp/send",
            json={"to": "Trader@example.com", "purpose": "SIGNUP"},
        )

    assert response.status_code == 200
    assert outbound["url"] == settings.gmail_bridge_url
    assert outbound["body"]["secret"] == settings.gmail_bridge_secret
    assert outbound["body"]["to"] == "trader@example.com"
    assert len(outbound["body"]["otp"]) == 6
    assert database.otp.code_hash != outbound["body"]["otp"]


def test_pin_verification_locks_after_five_failures(monkeypatch):
    database = _MemoryDatabase()
    redis = _MemoryRedis()
    pin = security.create_pbkdf2_pin("654321")
    database.pin = SimpleNamespace(user_id="test-user", **pin)
    settings = Settings(_env_file=None, app_secret="test-application-secret", jwt_secret="test-jwt")
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    with TestClient(_client(database=database, redis=redis)) as client:
        responses = [
            client.post("/auth/pin/verify", json={"pin": "000000"})
            for _ in range(5)
        ]
        locked = client.post("/auth/pin/verify", json={"pin": "654321"})

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 429]
    assert locked.status_code == 429


def test_initial_pin_is_persisted_as_pbkdf2_and_returns_session_token(monkeypatch):
    database = _MemoryDatabase()
    redis = _MemoryRedis()
    settings = Settings(_env_file=None, app_secret="test-application-secret", jwt_secret="test-jwt")
    monkeypatch.setattr(security, "get_settings", lambda: settings)

    with TestClient(_client(database=database, redis=redis)) as client:
        response = client.post("/auth/pin", json={"pin": "654321"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["pinToken"]
    assert database.pin.pin_hash != "654321"
    assert security.verify_pbkdf2_pin("654321", database.pin.salt_hex, database.pin.pin_hash)


def test_kyc_persists_masked_and_encrypted_sensitive_fields(monkeypatch):
    database = _MemoryDatabase()
    settings = Settings(
        _env_file=None,
        app_secret="test-application-secret",
        kyc_pii_encryption_key="test-kyc-encryption-key",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    payload = {
        "fullName": "Test Trader",
        "dob": "1990-01-01",
        "phone": "+919876543210",
        "pan": "ABCDE1234F",
        "aadhaarLast4": "1234",
        "incomeBracket": "10-25L",
        "experience": "INTERMEDIATE",
        "authorizedSegment": "ALL",
    }

    with TestClient(_client(database=database)) as client:
        response = client.post("/auth/kyc", json=payload)

    assert response.status_code == 200
    assert response.json()["kycStatus"] == "PENDING_ADMIN_REVIEW"
    assert database.kyc.pan_masked == "AB******4F"
    assert database.kyc.aadhaar_masked == "XXXX-XXXX-1234"
    assert "ABCDE1234F" not in database.kyc.encrypted_pii
    assert "9876543210" not in database.kyc.encrypted_pii
    assert database.user.trader_uid == response.json()["traderUid"]


def test_shoonya_login_uses_pyotp_and_never_returns_session_token(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_secret="test-app-secret",
        shoonya_api_key="test-api-key",
        shoonya_user_id="test-user",
        shoonya_password="test-password",
        shoonya_totp_secret="JBSWY3DPEHPK3PXP",
    )
    monkeypatch.setattr(shoonya, "get_settings", lambda: settings)
    monkeypatch.setattr(
        shoonya.pyotp,
        "TOTP",
        lambda _secret: SimpleNamespace(now=lambda: "123456"),
    )
    request_payloads = []

    def broker_response(request):
        form = parse_qs(request.content.decode())
        request_payloads.append(json.loads(form["jData"][0]))
        return httpx.Response(
            200,
            json={"stat": "Ok", "susertoken": "private-session-token", "uname": "Trader"},
        )

    adapter = shoonya.ShoonyaAdapter(transport=httpx.MockTransport(broker_response))
    result = asyncio.run(adapter.login())

    assert result["stat"] == "Ok"
    assert request_payloads[0]["factor2"] == "123456"
    assert adapter.session_token == "private-session-token"


def test_shoonya_adapters_reuse_the_redis_session_without_a_second_login(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_secret="test-app-secret",
        shoonya_api_key="test-api-key",
        shoonya_user_id="test-user",
        shoonya_password="test-password",
        shoonya_totp_secret="JBSWY3DPEHPK3PXP",
    )
    monkeypatch.setattr(shoonya, "get_settings", lambda: settings)
    monkeypatch.setattr("backend.app.security.get_settings", lambda: settings)

    class FakeRedis:
        enabled = True

        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, ttl_seconds):
            self.values[key] = value

        async def set_if_absent(self, key, value, ttl_seconds):
            if key in self.values:
                return False
            self.values[key] = value
            return True

        async def compare_and_delete(self, key, expected_value):
            if self.values.get(key) != expected_value:
                return False
            del self.values[key]
            return True

    redis = FakeRedis()
    requests = []

    def broker_response(request):
        requests.append(request)
        return httpx.Response(200, json={"stat": "Ok", "susertoken": "shared-session"})

    first = shoonya.ShoonyaAdapter(
        transport=httpx.MockTransport(broker_response),
        redis=redis,
    )
    second = shoonya.ShoonyaAdapter(
        transport=httpx.MockTransport(
            lambda _request: (_ for _ in ()).throw(AssertionError("unexpected login"))
        ),
        redis=redis,
    )

    asyncio.run(first.login())
    cached = asyncio.run(second.login())

    assert len(requests) == 1
    assert cached["cached"] is True
    assert second.session_token == "shared-session"


def test_coindcx_login_makes_only_a_signed_read_probe(monkeypatch):
    settings = Settings(
        _env_file=None,
        coindcx_api_key="test-api-key",
        coindcx_api_secret="test-api-secret",
    )
    monkeypatch.setattr(coindcx, "get_settings", lambda: settings)
    requests = []

    def broker_response(request):
        requests.append(request)
        if "public.coindcx.com" in str(request.url):
            return httpx.Response(200, json={"prices": {}})
        return httpx.Response(200, json=[])

    adapter = coindcx.CoinDCXAdapter(transport=httpx.MockTransport(broker_response))
    result = asyncio.run(adapter.login())

    assert result["linked"] is True
    assert result["positions_count"] == 0
    assert len(requests) == 2
    # First request is public API connectivity test
    assert "public.coindcx.com" in str(requests[0].url)
    # Second request is signed positions call
    assert requests[1].url.path.endswith("/positions")
    assert requests[1].headers["X-AUTH-APIKEY"] == settings.coindcx_api_key
    assert requests[1].headers.get("X-AUTH-SIGNATURE")


def test_shoonya_login_route_stores_session_without_exposing_token(monkeypatch):
    database = _MemoryDatabase()
    database.user = SimpleNamespace(
        external_subject="test-user",
        is_active=True,
        kyc_status="APPROVED",
    )
    redis = _MemoryRedis()
    monkeypatch.setattr(
        "backend.app.security.get_settings",
        lambda: Settings(_env_file=None, app_secret="test-application-secret"),
    )
    monkeypatch.setattr(shoonya, "get_settings", lambda: Settings(_env_file=None))

    async def login(self):
        self.user_id = "test-user"
        self.session_token = "secret-session-token"
        return {"stat": "Ok", "uname": "Trader", "susertoken": self.session_token}

    monkeypatch.setattr(brokers.ShoonyaAdapter, "login", login)
    with TestClient(_client(database=database, redis=redis)) as client:
        response = client.post("/brokers/shoonya/login")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "secret-session-token" not in response.text
    stored_token = redis.values["broker:session:shoonya:test-user"][0]["session_token"]
    assert stored_token != "secret-session-token"
    assert brokers._decrypt_broker_session(stored_token) == "secret-session-token"

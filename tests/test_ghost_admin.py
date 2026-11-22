import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from fastapi import HTTPException

from backend.app import security
from backend.app.config import Settings
from backend.app.integrations.database import PersistenceStatus
from backend.app.main import app
from backend.app.routers import ghost_admin
from backend.app.routers import auth
from backend.app.services.telegram_payments import parse_callback
from backend.app.routers.orders import _sync_subscription_access
from backend.app.services.risk import RiskGuardian


class FakeDatabase:
    enabled = True

    def __init__(self):
        self.user = SimpleNamespace(
            id="row-1",
            external_subject="user-1",
            provider="firebase",
            email="user@example.test",
            display_name="Test User",
            trader_uid="AR12345678",
            role="user",
            kyc_status="PENDING_ADMIN_REVIEW",
            is_active=True,
            is_vip_free=False,
            is_subscribed=False,
            subscription_expires_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.kyc = SimpleNamespace(
            user_id="user-1",
            full_name="Test User",
            status="PENDING_ADMIN_REVIEW",
            trial_expires_at=None,
        )
        self.configuration = None

    async def list_app_users(self, limit):
        return [self.user][:limit]

    async def list_kyc_records(self, user_ids):
        return [self.kyc] if self.kyc.user_id in user_ids else []

    async def get_app_user(self, subject):
        return self.user if subject == self.user.external_subject else None

    async def get_kyc_record(self, subject):
        return self.kyc if subject == self.kyc.user_id else None

    async def write_many(self, records):
        return PersistenceStatus(primary_applied=True, backup_applied=True)

    async def upsert_app_user(self, **values):
        for key, value in values.items():
            if hasattr(self.user, key):
                setattr(self.user, key, value)
        return PersistenceStatus(primary_applied=True, backup_applied=True)

    async def get_system_configuration(self):
        return self.configuration

    async def upsert_system_configuration(self, **values):
        self.configuration = SimpleNamespace(
            id="global",
            global_monthly_fee_inr=values["global_monthly_fee_inr"],
            updated_by=values["updated_by"],
        )
        return PersistenceStatus(primary_applied=True, backup_applied=True)


def test_ghost_admin_management_requires_password_and_syncs_user_access(monkeypatch):
    settings = Settings(
        _env_file=None,
        admin_password="test-admin-password",
        jwt_secret="test-signing-secret-with-enough-entropy",
    )
    database = FakeDatabase()
    guardian = RiskGuardian()
    monkeypatch.setattr(ghost_admin, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(app.state, "database", database, raising=False)
    monkeypatch.setitem(
        app.dependency_overrides,
        ghost_admin.get_risk_guardian,
        lambda: guardian,
    )

    client = TestClient(app)
    try:
        assert client.get("/admin/ghost/users").status_code == 401
        assert client.get("/admin/ghost/configuration").status_code == 401
        assert client.post(
            "/admin/ghost/users/user-1/subscription/extend",
            json={"months": 1},
        ).status_code == 401
        assert client.post(
            "/admin/ghost/login",
            json={"password": "not-the-admin-password"},
        ).status_code == 401

        login = client.post(
            "/admin/ghost/login",
            json={"password": "test-admin-password"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        users = client.get("/admin/ghost/users", headers=headers)
        assert users.status_code == 200
        assert users.json()["users"][0]["user_name"] == "Test User"

        approved = client.post(
            "/admin/ghost/users/user-1/kyc/approve", headers=headers
        )
        assert approved.status_code == 200
        assert database.user.kyc_status == "APPROVED"
        assert database.kyc.status == "APPROVED"
        assert not guardian.user_access_allowed("user-1")

        vip = client.post(
            "/admin/ghost/users/user-1/vip",
            headers=headers,
            json={"is_vip_free": True},
        )
        assert vip.status_code == 200
        assert guardian.user_access_allowed("user-1")

        revoked = client.post(
            "/admin/ghost/users/user-1/access",
            headers=headers,
            json={"is_active": False},
        )
        assert revoked.status_code == 200
        assert not guardian.user_access_allowed("user-1")

        restored = client.post(
            "/admin/ghost/users/user-1/access",
            headers=headers,
            json={"is_active": True},
        )
        assert restored.status_code == 200
        assert guardian.user_access_allowed("user-1")

        tomorrow = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
        extended = client.post(
            "/admin/ghost/users/user-1/subscription",
            headers=headers,
            json={"expires_on": tomorrow},
        )
        assert extended.status_code == 200
        assert database.user.is_vip_free is False
        assert database.user.subscription_expires_at is not None
        assert database.user.is_subscribed is True

        term_extended = client.post(
            "/admin/ghost/users/user-1/subscription/extend",
            headers=headers,
            json={"months": 6},
        )
        assert term_extended.status_code == 200
        assert term_extended.json()["months_added"] == 6
        assert database.user.is_subscribed is True
        assert guardian.user_access_allowed("user-1")

        custom_activated = client.post(
            "/api/admin/activate-user",
            headers=headers,
            json={"subject": "user-1", "months": 5},
        )
        assert custom_activated.status_code == 200
        assert custom_activated.json()["months_added"] == 5
        assert database.user.is_subscribed is True

        saved = client.post(
            "/admin/ghost/configuration",
            headers=headers,
            json={"global_monthly_fee_inr": 499.5},
        )
        assert saved.status_code == 200
        assert client.get("/admin/ghost/pricing").json()["global_monthly_fee_inr"] == 499.5
    finally:
        client.close()


def test_subscription_term_addition_clamps_month_end():
    assert ghost_admin._add_calendar_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert ghost_admin._add_calendar_months(date(2025, 11, 30), 6) == date(2026, 5, 30)


def test_telegram_callback_round_trips_custom_months():
    callback = "APPROVE|payment-123"
    parsed = parse_callback(callback)
    assert parsed == ("APPROVE", "payment-123")
    assert parse_callback("APPROVE|") is None


def test_authenticated_subscription_upi_intent_uses_server_config_and_price(monkeypatch):
    settings = Settings(
        _env_file=None,
        admin_upi_id="merchant@example",
        admin_name="ARAF Merchant",
    )
    database = FakeDatabase()
    database.configuration = SimpleNamespace(global_monthly_fee_inr=499.5)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))
    client = TestClient(app)
    try:
        assert client.get("/auth/subscription/upi-intent").status_code == 401
    finally:
        client.close()

    intent = asyncio.run(auth.create_subscription_upi_intent(request, "user-1"))

    assert intent["success"] is True
    assert intent["amount_inr"] == 499.5
    assert intent["qr_svg"].startswith("<svg")
    query = parse_qs(urlsplit(intent["intent_uri"]).query)
    assert query == {
        "pa": ["merchant@example"],
        "pn": ["ARAF Merchant"],
        "am": ["499.5"],
        "cu": ["INR"],
        "tn": ["ARAF-Terminal-Sub-user-1-1M"],
    }


def test_risk_guardian_blocks_entry_after_subscription_access_sync():
    guardian = RiskGuardian()
    guardian.sync_user_access("revoked-user", False)

    allowed, reason, _ = guardian.precheck_entry(
        subject="revoked-user",
        entry_price=100,
        stop_loss=99,
        quantity=1,
        is_auto_trade=False,
        side="BUY",
    )

    assert not allowed
    assert reason == "USER_ACCESS_NOT_ENTITLED"


def test_order_access_sync_reads_persisted_entitlement_before_entry():
    database = FakeDatabase()
    guardian = RiskGuardian()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(database=database))
    )

    try:
        asyncio.run(_sync_subscription_access(request, "user-1", guardian))
    except HTTPException as error:
        assert error.status_code == 403
        assert error.detail == {"code": "USER_KYC_NOT_APPROVED"}
    else:
        raise AssertionError("Pending KYC user must not pass order access sync")

    database.user.kyc_status = "APPROVED"
    database.user.is_vip_free = True
    asyncio.run(_sync_subscription_access(request, "user-1", guardian))
    assert guardian.user_access_allowed("user-1")

    database.user.is_active = False
    try:
        asyncio.run(_sync_subscription_access(request, "user-1", guardian))
    except HTTPException as error:
        assert error.status_code == 403
        assert error.detail == {"code": "USER_ACCESS_REVOKED"}
    else:
        raise AssertionError("Revoked user must not pass order access sync")

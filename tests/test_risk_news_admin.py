import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from backend.app.config import Settings
from backend.app.integrations.database import PersistenceStatus
from backend.app.routers.orders import place_order
from backend.app.routers.risk import sync_daily_pnl
from backend.app.schemas import OrderRequest, RiskPnlSync
from backend.app.services.news import (
    NewsEngine,
    _coinpaprika,
    normalize_news_item,
    priority_matches,
)
from backend.app.services.risk import RiskGuardian
from backend.app.services.risk_persistence import load_risk_state


def test_priority_news_normalization_captures_fii_and_crude_oil():
    item = normalize_news_item(
        "test",
        title="FII flows rise as crude oil prices fall",
        url="https://example.test/news/1",
        published_at="2026-11-21T10:00:00Z",
    )

    assert item is not None
    assert item["priority"]
    assert {"FII", "crude oil"} <= set(item["priorityReasons"])


def test_news_engine_polls_concurrently_and_deduplicates_by_source_item():
    requests = []

    async def handler(request):
        requests.append(request.url.host)
        if request.url.host == "api.coinpaprika.com":
            return httpx.Response(200, json=[])
        published = format_datetime(datetime.now(timezone.utc), usegmt=True)
        return httpx.Response(
            200,
            content=(
                b"<rss><channel><item><title>FII crude oil flows</title>"
                + f"<link>https://example.test/market-news</link><pubDate>{published}</pubDate>".encode()
                + b"</item></channel></rss>"
            ),
        )

    async def run():
        engine = NewsEngine(
            Settings(_env_file=None, news_google_rss_enabled=False),
            transport=httpx.MockTransport(handler),
        )
        first = await engine.poll_due()
        second = await engine.poll_due()
        return first, second

    first, second = asyncio.run(run())
    assert len(requests) == 5
    assert len(first["items"]) == 3
    assert len(second["items"]) == 3
    assert first["priority_guard_active"]
    assert "coinpaprika" in first["providers"]


def test_coinpaprika_free_adapter_returns_events_and_priority_market_signals():
    calls = []
    published = datetime.now(timezone.utc).isoformat()

    async def handler(request):
        calls.append(request)
        if request.url.host != "api.coinpaprika.com":
            return httpx.Response(200, content=b"<rss><channel></channel></rss>")
        if request.url.path.endswith("/tickers"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "btc-bitcoin",
                        "name": "Bitcoin",
                        "symbol": "BTC",
                        "last_updated": published,
                        "quotes": {"USD": {"percent_change_24h": -18.5}},
                    },
                    {
                        "id": "eth-ethereum",
                        "name": "Ethereum",
                        "symbol": "ETH",
                        "quotes": {"USD": {"percent_change_24h": 2.0}},
                    },
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "name": "Bitcoin whale activity update",
                    "description": "A whale-related event for Bitcoin.",
                    "date": published,
                    "link": "https://example.test/bitcoin-event",
                }
            ],
        )

    async def run():
        settings = Settings(_env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _coinpaprika(client, settings)

    items = asyncio.run(run())

    assert len(calls) == 2
    assert all(call.url.host == "api.coinpaprika.com" for call in calls)
    assert all("authorization" not in call.headers for call in calls)
    assert {item["source"] for item in items} == {"coinpaprika"}
    assert any("whale" in item["priorityReasons"] for item in items)
    assert any("crypto crash" in item["priorityReasons"] for item in items)
    assert "whale" in priority_matches("Large whale movement reported")

    async def poll_engine():
        engine = NewsEngine(
            Settings(_env_file=None, news_google_rss_enabled=False),
            transport=httpx.MockTransport(handler),
        )
        return await engine.poll_due()

    snapshot = asyncio.run(poll_engine())
    assert snapshot["priority_guard_active"]
    assert any(item["source"] == "coinpaprika" for item in snapshot["priority_events"])


def test_coinpaprika_adapter_always_uses_the_public_keyless_api():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    async def run():
        settings = Settings(_env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _coinpaprika(client, settings)

    asyncio.run(run())

    assert len(calls) == 2
    assert all(call.url.host == "api.coinpaprika.com" for call in calls)
    assert all("authorization" not in call.headers for call in calls)


def test_risk_pnl_sync_uses_actual_net_pnl_and_breach_remains_locked():
    guardian = RiskGuardian()
    initial = guardian.sync_pnl("user-1", 5000)
    assert initial.daily_pnl_inr == 5000
    assert not initial.is_hard_locked

    breached = guardian.sync_pnl("user-1", -2500)
    recovered = guardian.sync_pnl("user-1", 1000)

    assert breached.is_hard_locked
    assert recovered.is_hard_locked
    assert recovered.daily_pnl_inr == 1000
    with pytest.raises(PermissionError):
        guardian.reset("user-1")


@pytest.mark.parametrize(
    ("client_pnl", "server_pnl", "expected_locked", "expected_pnl"),
    [
        (0, -3000, True, -3000),
        (-10000, 0, False, 0),
    ],
)
def test_risk_pnl_endpoint_uses_persisted_server_paper_pnl(
    client_pnl, server_pnl, expected_locked, expected_pnl
):
    class FakeDatabase:
        enabled = True

        async def get_risk_config(self, user_id):
            return None

        async def get_risk_state(self, user_id, trading_day):
            return None

        async def get_daily_paper_pnl_inr(self, user_id, start_at, end_at):
            return server_pnl

        async def upsert_risk_config(self, **values):
            return PersistenceStatus(primary_applied=True)

        async def upsert_risk_state(self, **values):
            return PersistenceStatus(primary_applied=True)

    class FakeRedis:
        enabled = False

    guardian = RiskGuardian(max_daily_loss_amount=100_000)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                database=FakeDatabase(),
                redis=FakeRedis(),
            )
        )
    )

    result = asyncio.run(
        sync_daily_pnl(
            RiskPnlSync(daily_pnl_inr=client_pnl),
            request,
            subject="server-pnl-user",
            guardian=guardian,
        )
    )

    assert result["risk"]["daily_pnl_inr"] == expected_pnl
    assert result["risk"]["is_hard_locked"] is expected_locked


def test_risk_settings_and_loss_watermark_lock_for_24_hours():
    guardian = RiskGuardian(max_daily_trades=20)
    state = guardian.configure("risk-lock-user", 100_000, 2.5, 12, 1.5)
    expires_at = state.risk_lock_expires_at
    assert expires_at is not None
    assert 23 * 60 * 60 < (expires_at - datetime.now(timezone.utc)).total_seconds() <= 24 * 60 * 60

    with pytest.raises(PermissionError, match="locked for 24 hours"):
        guardian.configure("risk-lock-user", 100_000, 5, 20, 2)

    locked = guardian.sync_pnl("risk-lock-user", -2500)
    assert locked.risk_lock_expires_at >= expires_at
    assert locked.daily_loss_lock_started_at is not None
    restored = RiskGuardian(max_daily_trades=20).restore_persisted(
        "risk-lock-user", RiskGuardian.to_record(locked)
    )
    assert restored.is_hard_locked
    assert restored.risk_lock_expires_at == locked.risk_lock_expires_at
    assert restored.risk_lock_expires_at > expires_at

    unlocked = guardian.reset("risk-lock-user", admin_override=True)
    assert not unlocked.is_hard_locked
    assert unlocked.daily_pnl_inr == 0
    assert unlocked.risk_lock_expires_at is None


def test_daily_loss_limit_uses_exact_inr_amount_and_locks_at_boundary():
    guardian = RiskGuardian(max_daily_loss_amount=100_000)
    configured = guardian.configure("exact-loss-user", 100_000, 1250, 10, 2)
    assert configured.daily_loss_limit_inr == -1250

    below_limit = guardian.sync_pnl("exact-loss-user", -1249.99)
    assert not below_limit.is_hard_locked
    breached = guardian.sync_pnl("exact-loss-user", -1250)
    assert breached.is_hard_locked
    assert breached.locked_reason == "DAILY_LOSS_LIMIT_BREACHED"
    assert breached.risk_lock_expires_at is not None
    with pytest.raises(PermissionError, match="locked for 24 hours"):
        guardian.configure("exact-loss-user", 100_000, 5000, 10, 2)


def test_daily_loss_lock_extends_24_hours_from_breach_without_sliding():
    guardian = RiskGuardian(max_daily_loss_amount=100_000)
    state = guardian.configure("breach-time-user", 100_000, 1250, 10, 2)
    state.risk_lock_expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)

    breached = guardian.sync_pnl("breach-time-user", -1250)
    expiry = breached.risk_lock_expires_at
    started_at = breached.daily_loss_lock_started_at
    assert started_at is not None
    assert expiry >= datetime.now(timezone.utc) + timedelta(hours=23, minutes=59)

    later_update = guardian.sync_pnl("breach-time-user", -1500)
    assert later_update.risk_lock_expires_at == expiry
    assert later_update.daily_loss_lock_started_at == started_at


def test_redis_only_risk_lock_survives_trading_day_change(monkeypatch):
    today = "2026-11-22"
    previous_day = "2026-11-21"
    now = datetime.now(timezone.utc)
    guardian = RiskGuardian(max_daily_loss_amount=100_000)
    state = guardian.configure("redis-day-user", 100_000, 1250, 10, 2)
    state.daily_pnl_inr = -1250
    state.is_hard_locked = True
    state.locked_reason = "DAILY_LOSS_LIMIT_BREACHED"
    state.daily_loss_lock_started_at = now - timedelta(hours=1)
    state.risk_lock_expires_at = now + timedelta(hours=23)
    record = guardian.to_record(state)
    record["trading_day"] = previous_day

    class FakeRedis:
        enabled = True

        def __init__(self):
            self.values = {f"risk:redis-day-user:{previous_day}": record}

        async def get_json(self, key):
            return self.values.get(key)

        async def set_json(self, key, value, ttl_seconds):
            self.values[key] = value

    class FakeDatabase:
        enabled = False

    monkeypatch.setattr(guardian, "_today", lambda: today)
    monkeypatch.setattr(
        RiskGuardian,
        "current_trading_day",
        classmethod(lambda cls: today),
    )
    redis = FakeRedis()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(redis=redis, database=FakeDatabase())
        )
    )

    restored = asyncio.run(load_risk_state(request, "redis-day-user", guardian))

    assert restored.is_hard_locked
    assert restored.locked_reason == "DAILY_LOSS_LIMIT_BREACHED"
    assert restored.daily_pnl_inr == -1250
    assert restored.risk_lock_expires_at == state.risk_lock_expires_at
    assert "risk:redis-day-user:state" in redis.values


def test_redis_only_risk_read_failure_fails_closed():
    class FailedRedis:
        enabled = True

        async def get_json(self, key):
            from redis.exceptions import RedisError

            raise RedisError("unavailable")

    class FakeDatabase:
        enabled = False

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(redis=FailedRedis(), database=FakeDatabase())
        )
    )

    with pytest.raises(RuntimeError, match="Risk state cache is unavailable"):
        asyncio.run(load_risk_state(request, "redis-failed-user", RiskGuardian()))


def test_daily_trade_limit_locks_and_cannot_be_reset():
    guardian = RiskGuardian(max_daily_trades=1, max_per_trade_risk_pct=2)
    allowed, _, state = guardian.precheck_entry(
        subject="user-2",
        entry_price=100,
        stop_loss=99,
        quantity=1,
        is_auto_trade=False,
        side="BUY",
    )

    assert allowed
    assert state.is_hard_locked
    assert state.locked_reason == "DAILY_TRADES_LIMIT_REACHED"
    with pytest.raises(PermissionError):
        guardian.reset("user-2")


def test_global_kill_switch_blocks_manual_entry_orders():
    class FakeDatabase:
        enabled = True

        async def get_order(self, subject, client_order_id):
            return None

        async def get_admin_control(self):
            return type("Control", (), {"global_kill_switch": True})()

    class FakeHub:
        enabled = False

    state = type(
        "State",
        (),
        {
            "database": FakeDatabase(),
            "redis": FakeHub(),
            "mongo": FakeHub(),
            "news_engine": None,
        },
    )()
    request = type("Request", (), {"app": type("App", (), {"state": state})()})()
    payload = OrderRequest(
        symbol="RELIANCE",
        side="BUY",
        quantity=1,
        mode="PAPER",
        entry_price=100,
        stop_loss=99,
        client_order_id="KILLTEST_123",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            place_order(
                payload,
                request,
                subject="user-kill-test",
                guardian=RiskGuardian(),
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail == {"code": "GLOBAL_KILL_SWITCH_ACTIVE"}


def test_order_route_rejects_risk_reward_below_two_to_one():
    class FakeDatabase:
        enabled = True

        async def get_order(self, subject, client_order_id):
            return None

        async def get_admin_control(self):
            return None

    class DisabledIntegration:
        enabled = False

    state = type(
        "State",
        (),
        {
            "database": FakeDatabase(),
            "redis": DisabledIntegration(),
            "mongo": DisabledIntegration(),
        },
    )()
    request = type("Request", (), {"app": type("App", (), {"state": state})()})()
    payload = OrderRequest(
        symbol="RELIANCE",
        side="BUY",
        quantity=1,
        mode="PAPER",
        entry_price=100,
        stop_loss=99,
        take_profit=101,
        client_order_id="RRTEST_001",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            place_order(
                payload,
                request,
                subject="rr-test-user",
                guardian=RiskGuardian(),
            )
        )

    assert error.value.status_code == 403
    assert error.value.detail == {
        "code": "MINIMUM_RISK_REWARD_NOT_MET",
        "minimum_rr": 2,
    }

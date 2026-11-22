import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.services.automation import OpportunityHub, calculate_trade_plan


def _report(score=80, side="BUY"):
    return {
        "fusionScore": score,
        "side": side,
        "symbol": "TEST",
        "orderKey": "TEST",
        "exchange": "NSE",
        "category": "indian",
        "entry": 100,
        "allRequiredPass": True,
        "isLiveVerified": True,
        "autoTradeEligible": score >= 90,
        "apexSetup": {"allowed": score >= 70},
        "bengaliSummary": "সব আবশ্যক গেট পাস।",
        "engines": [
            {"required": True, "status": "PASS", "explainBn": "নিউজ ও ঝুঁকি গেট পাস।"}
        ],
    }


def test_manual_alert_tiers_start_at_70_and_auto_trade_requires_90():
    async def run():
        hub = OpportunityHub()
        await hub.set_settings("user-1", {"enabled": False, "dailyBudgetInr": 10_000})
        below_threshold = await hub.publish(
            subject="user-1", report=_report(69), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=True, global_kill_switch=False,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        )
        assert below_threshold is None

        standard = await hub.publish(
            subject="user-1", report=_report(70), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=True, global_kill_switch=False,
            lifecycle_ready=True,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        )
        assert standard is not None
        assert standard["tierLabel"] == "Standard Opportunity (70%+)"
        assert standard["autoTradeEligible"] is False
        assert standard["executionEligible"] is True
        assert standard["bengaliReason"].startswith("সব আবশ্যক")

        high_conviction = await hub.publish(
            subject="user-1", report=_report(80), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=True, global_kill_switch=False,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        )
        assert high_conviction is not None
        assert high_conviction["tierLabel"] == "High-Conviction Opportunity (80%+)"
        assert high_conviction["autoTradeEligible"] is False

        await hub.set_settings("user-1", {"enabled": True, "dailyBudgetInr": 10_000})
        prime_while_on = await hub.publish(
            subject="user-1", report=_report(90), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=True, global_kill_switch=False,
            lifecycle_ready=True,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        )
        assert prime_while_on is not None
        assert prime_while_on["tierLabel"] == "Prime Apex Opportunity (90%+)"
        assert prime_while_on["autoTradeEligible"] is True
        assert prime_while_on["executionEligible"] is True

        prime_while_off = await hub.publish(
            subject="user-2", report=_report(90), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=False, global_kill_switch=False,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        )
        assert prime_while_off is not None
        assert prime_while_off["autoTradeEligible"] is False
        assert prime_while_off["executionEligible"] is True

    asyncio.run(run())


def test_alert_has_bengali_reason_and_expires_at_15_seconds():
    async def run():
        hub = OpportunityHub()
        now = datetime.now(timezone.utc)
        alert = await hub.publish(
            subject="user-1", report=_report(80), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=False, global_kill_switch=False,
            capital_inr=100_000, risk_pct=2, atr_value=1, now=now,
        )
        assert alert is not None
        assert alert["bengaliReason"]
        assert datetime.fromisoformat(alert["expiresAt"]) == now + timedelta(seconds=15)
        claimed = await hub.claim("user-1", alert["id"], now=now + timedelta(seconds=15))
        assert claimed is None

    asyncio.run(run())


def test_paper_option_opportunity_keeps_bearish_signal_and_buys_the_put():
    async def run():
        report = _report(85, side="BUY")
        report.update({
            "signalSide": "SELL",
            "optionType": "PE",
            "underlying": "NIFTY",
        })
        hub = OpportunityHub()
        alert = await hub.publish(
            subject="option-user",
            report=report,
            trusted_feed=True,
            scanner_gate_passed=True,
            news_guard_active=False,
            global_auto_trade_enabled=False,
            global_kill_switch=False,
            capital_inr=100_000,
            risk_pct=2,
            atr_value=1,
        )

        assert alert is not None
        assert alert["side"] == "BUY"
        assert alert["direction"] == "SELL"
        assert alert["optionType"] == "PE"
        assert alert["underlying"] == "NIFTY"

    asyncio.run(run())


def test_manual_alert_gates_fail_closed_for_untrusted_or_blocked_data():
    async def run():
        hub = OpportunityHub()
        assert await hub.publish(
            subject="user-1", report=_report(90), trusted_feed=False,
            scanner_gate_passed=True, news_guard_active=False,
            global_auto_trade_enabled=True, global_kill_switch=False,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        ) is None
        assert await hub.publish(
            subject="user-1", report=_report(90), trusted_feed=True,
            scanner_gate_passed=True, news_guard_active=True,
            global_auto_trade_enabled=True, global_kill_switch=False,
            capital_inr=100_000, risk_pct=2, atr_value=1,
        ) is None

    asyncio.run(run())


def test_daily_budget_risk_limit_and_atr_levels_for_india_and_crypto():
    indian = calculate_trade_plan(
        side="BUY", entry=100, atr_value=1, capital_inr=100_000,
        daily_budget_inr=10_000, max_risk_pct=2, lot_size=10,
    )
    assert indian is not None
    assert indian["quantity"] == 100
    assert indian["stop_loss"] == 98.5
    assert indian["take_profit"] == 103
    assert indian["capital_used_inr"] <= 10_000
    assert indian["risk_inr"] <= 2_000

    crypto = calculate_trade_plan(
        side="SELL", entry=1000, atr_value=10, capital_inr=100_000,
        daily_budget_inr=50_000, max_risk_pct=2, fx_to_inr=86,
        lot_size=0.001,
    )
    assert crypto is not None
    assert crypto["quantity"] == 0.581
    assert crypto["stop_loss"] == 1015
    assert crypto["take_profit"] == 970
    assert crypto["capital_used_inr"] <= 50_000
    assert crypto["risk_inr"] <= 2_000

    limited_crypto = calculate_trade_plan(
        side="SELL", entry=1000, atr_value=10, capital_inr=100_000,
        daily_budget_inr=50_000, used_budget_inr=49_000, max_risk_pct=2,
        fx_to_inr=86, lot_size=0.001,
    )
    assert limited_crypto is not None
    assert limited_crypto["quantity"] == 0.011
    assert limited_crypto["capital_used_inr"] + 49_000 <= 50_000
    assert calculate_trade_plan(
        side="SELL", entry=1000, atr_value=10, capital_inr=100_000,
        daily_budget_inr=50_000, used_budget_inr=50_000,
        fx_to_inr=86, lot_size=0.001,
    ) is None
    assert calculate_trade_plan(
        side="BUY", entry=100, atr_value=1, capital_inr=100_000,
        daily_budget_inr=10_000, strategy_stop=99, strategy_target=101,
    ) is None


def test_daily_trade_limit_is_user_configurable_above_three():
    from pydantic import ValidationError

    from backend.app.config import APEX_MAX_DAILY_SETUPS, DEFAULT_MAX_DAILY_TRADES, Settings
    from backend.app.schemas import RiskConfig
    from backend.app.services.risk import RiskGuardian

    state = RiskGuardian().get_state("default-cap-user")
    assert state.max_trades_per_day == DEFAULT_MAX_DAILY_TRADES
    assert Settings(_env_file=None).max_daily_trades == APEX_MAX_DAILY_SETUPS
    assert RiskConfig(capital_inr=100_000, max_trades_per_day=4).max_trades_per_day == 4
    with pytest.raises(ValidationError):
        RiskConfig(capital_inr=100_000, max_trades_per_day=APEX_MAX_DAILY_SETUPS + 1)


def test_risk_guardian_honors_user_daily_trade_limit():
    from backend.app.services.risk import RiskGuardian

    guardian = RiskGuardian(max_daily_trades=20)
    state = guardian.configure("capped-user", 100_000, 2.5, 20, 2)
    assert state.max_trades_per_day == 20

    for _ in range(20):
        allowed, _, _ = guardian.precheck_entry(
            subject="capped-user",
            entry_price=100,
            stop_loss=99,
            quantity=1,
            is_auto_trade=True,
            side="BUY",
        )
        assert allowed
    allowed, reason, state = guardian.precheck_entry(
        subject="capped-user",
        entry_price=100,
        stop_loss=99,
        quantity=1,
        is_auto_trade=True,
        side="BUY",
    )
    assert not allowed
    assert reason == "DAILY_TRADES_LIMIT_REACHED"
    assert state.trades_count == 20


def test_server_claim_rejects_below_threshold_alert_without_consuming_it():
    from backend.app.routers.automation import claim_opportunity

    async def run():
        hub = OpportunityHub()
        alert = {
            "id": "below-threshold",
            "fusionScore": 79,
            "executionEligible": False,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(seconds=15)).isoformat(),
        }
        hub._opportunities["user-1"] = [alert]
        database = SimpleNamespace(
            enabled=True,
            get_admin_control=lambda: _async_value(None),
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(database=database, opportunity_hub=hub)
            )
        )
        with pytest.raises(HTTPException) as error:
            await claim_opportunity(alert["id"], request, subject="user-1")
        assert error.value.status_code == 403
        assert (await hub.list_active("user-1"))[0]["id"] == alert["id"]

    async def _inner():
        await run()

    asyncio.run(_inner())


def test_server_manual_exit_closes_auto_position_and_blocks_same_setup(monkeypatch):
    from backend.app.routers.automation import manually_exit_paper_position
    from backend.app.routers.orders import _place_order
    from backend.app.schemas import OrderRequest
    from backend.app.services.risk import RiskGuardian

    class PersistenceStatus:
        primary_applied = True
        backup_applied = False

        def as_dict(self):
            return {"primary_applied": True, "backup_applied": False}

    class FakeDatabase:
        enabled = True

        def __init__(self):
            self.position = SimpleNamespace(
                id="paper-position-1",
                user_id="manual-exit-user",
                client_order_id="AUTO_ORDER_1",
                symbol="BTCUSDT",
                exchange="COINDCX",
                broker_token=None,
                side="BUY",
                quantity=2,
                entry_price=100,
                stop_loss=95,
                take_profit=110,
                trailing_distance=2,
                favorable_price=100,
                status="OPEN",
                is_auto_trade=True,
                exit_price=None,
                realized_pnl=0,
                opened_at=datetime.now(timezone.utc),
                closed_at=None,
                exit_reason=None,
                manual_exit_blocked_until=None,
                pnl_fx_to_inr=85,
            )

        async def get_user_paper_position(self, user_id, position_id):
            if user_id == self.position.user_id and position_id == self.position.id:
                return self.position
            return None

        async def get_risk_config(self, user_id):
            return None

        async def get_risk_state(self, user_id, trading_day):
            return None

        async def upsert_risk_config(self, **values):
            return PersistenceStatus()

        async def upsert_risk_state(self, **values):
            return PersistenceStatus()

        async def upsert_paper_position(self, **values):
            for name, value in values.items():
                setattr(self.position, name, value)
            return PersistenceStatus()

        async def has_manual_exit_block(self, user_id, symbol, exchange, side):
            return bool(
                user_id == self.position.user_id
                and symbol == self.position.symbol
                and exchange == self.position.exchange
                and side == self.position.side
                and self.position.exit_reason == "MANUAL_EXIT"
                and self.position.manual_exit_blocked_until > datetime.now(timezone.utc)
            )

    class FakeRedis:
        enabled = False

    class FakeCoinDCX:
        async def get_quote(self, symbol):
            assert symbol == "BTCUSDT"
            return SimpleNamespace(price=90, source="COINDCX")

    monkeypatch.setattr(
        "backend.app.routers.automation.CoinDCXAdapter", FakeCoinDCX
    )

    async def run():
        database = FakeDatabase()
        guardian = RiskGuardian(max_daily_loss_amount=100_000)
        guardian.configure("manual-exit-user", 100_000, 1000, 10, 2)
        hub = OpportunityHub()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    database=database,
                    redis=FakeRedis(),
                    risk_guardian=guardian,
                    opportunity_hub=hub,
                    paper_position_locks={},
                )
            )
        )

        result = await manually_exit_paper_position(
            "paper-position-1", request, subject="manual-exit-user"
        )
        assert result["status"] == "CLOSED"
        assert result["exit_reason"] == "MANUAL_EXIT"
        assert result["realized_pnl"] == -20
        assert database.position.status == "CLOSED"
        assert guardian.get_state("manual-exit-user").daily_pnl_inr == -1700
        assert guardian.get_state("manual-exit-user").is_hard_locked
        assert await hub.is_manual_exit_setup_blocked(
            "manual-exit-user",
            order_key="BTCUSDT",
            exchange="COINDCX",
            side="BUY",
        )

        reentry_report = _report(90)
        reentry_report.update({
            "symbol": "BTCUSDT",
            "orderKey": "BTCUSDT",
            "exchange": "COINDCX",
            "category": "crypto",
        })
        assert await hub.publish(
            subject="manual-exit-user",
            report=reentry_report,
            trusted_feed=True,
            scanner_gate_passed=True,
            news_guard_active=False,
            global_auto_trade_enabled=True,
            global_kill_switch=False,
            lifecycle_ready=True,
            capital_inr=100_000,
            risk_pct=2,
            atr_value=1,
        ) is None

        with pytest.raises(HTTPException) as reentry_error:
            await _place_order(
                OrderRequest(
                    symbol="BTCUSDT",
                    exchange="COINDCX",
                    side="BUY",
                    quantity=2,
                    mode="PAPER",
                    client_order_id="REENTRY_ORDER_123",
                ),
                request,
                "manual-exit-user",
                guardian,
            )
        assert reentry_error.value.detail["code"] == "MANUAL_EXIT_REENTRY_LOCK"

        with pytest.raises(HTTPException) as error:
            await manually_exit_paper_position(
                "paper-position-1", request, subject="manual-exit-user"
            )
        assert error.value.status_code == 409

    asyncio.run(run())


def test_server_rejects_reenabling_auto_trade_under_risk_lock(monkeypatch):
    from backend.app.routers.automation import (
        AutomationSettingsPayload,
        update_automation_settings,
    )
    from backend.app.services.risk import RiskGuardian

    class FakeDatabase:
        enabled = True

        async def get_risk_config(self, user_id):
            return None

        async def get_risk_state(self, user_id, trading_day):
            return None

        async def get_admin_control(self):
            return None

    class FakeRedis:
        enabled = False

    async def load_settings(request, subject):
        return {"enabled": False, "dailyBudgetInr": 10000, "allowedMarkets": "ALL"}

    monkeypatch.setattr(
        "backend.app.routers.automation._load_persisted_settings", load_settings
    )

    async def run():
        guardian = RiskGuardian(max_daily_loss_amount=100_000)
        guardian.configure("locked-toggle-user", 100_000, 1250, 10, 2)
        guardian.sync_pnl("locked-toggle-user", -1250)
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    database=FakeDatabase(),
                    redis=FakeRedis(),
                    risk_guardian=guardian,
                )
            )
        )
        with pytest.raises(HTTPException) as error:
            await update_automation_settings(
                AutomationSettingsPayload(enabled=True),
                request,
                subject="locked-toggle-user",
            )
        assert error.value.status_code == 403
        assert error.value.detail["code"] == "USER_RISK_HARD_LOCK_ACTIVE"

    asyncio.run(run())


async def _async_value(value):
    return value

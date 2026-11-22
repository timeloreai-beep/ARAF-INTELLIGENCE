import math

from backend.app.quant.greeks import black_scholes
from backend.app.quant.confluence import evaluate_five_layers
from backend.app.services.risk import RiskGuardian


def test_call_greeks_are_finite_and_positive():
    result = black_scholes(100, 100, 0.05, 0.20, 0.25)
    assert result.price > 0
    assert 0 < result.delta < 1
    assert result.gamma > 0
    assert result.vega > 0
    assert math.isfinite(result.theta)


def test_five_layer_gate_requires_all_layers():
    blocked = evaluate_five_layers(
        macro_green=True,
        smc_green=True,
        mtf_green=True,
        volume_green=False,
        risk_green=True,
    )
    allowed = evaluate_five_layers(
        macro_green=True,
        smc_green=True,
        mtf_green=True,
        volume_green=True,
        risk_green=True,
    )
    assert not blocked.allowed
    assert allowed.allowed


def test_risk_guardian_blocks_per_trade_risk_over_cap():
    guardian = RiskGuardian()
    allowed, reason, state = guardian.precheck_entry(
        subject="test",
        entry_price=100,
        stop_loss=90,
        quantity=1000,
        is_auto_trade=False,
    )
    assert not allowed
    assert "PER_TRADE_RISK_CAP" in reason
    assert state.trades_count == 0


def test_risk_guardian_restores_persisted_daily_lock():
    guardian = RiskGuardian()
    state = guardian.restore_persisted(
        "test",
        {
            "capital_inr": 100000,
            "max_daily_loss_amount": 2500,
            "max_per_trade_risk_pct": 2,
            "max_trades_per_day": 5,
            "daily_pnl_inr": -3000,
            "trades_count": 2,
            "trading_day": guardian._today(),
            "is_hard_locked": True,
            "locked_reason": "DAILY_LOSS_LIMIT_BREACHED",
        },
    )

    assert state.is_hard_locked
    assert guardian.to_record(state)["daily_pnl_inr"] == -3000

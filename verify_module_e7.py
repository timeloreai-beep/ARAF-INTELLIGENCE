#!/usr/bin/env python3
"""
Module E7 Verification Script
Tests all 5 capital protection scenarios:
(a) Low-profit trade that fails after Brokerage/Taxes/Slippage -> VETOED with exact math!
(b) Wide-spread / fake breakout trap even with high signal score -> VETOED!
(c) Duplicate order attempt for same symbol/side -> BLOCKED!
(d) Weekly loss limit exceeded -> BLOCKED!
(e) Healthy net-profitable setup -> APPROVED!
"""

import sys
import math
from backend.app.services.cost_calculator import CostCalculator, BrokerType, SegmentType
from backend.app.services.risk import RiskGuardian, UserRiskState
from datetime import datetime, timezone


def print_header(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_result(scenario: str, passed: bool, details: str = ""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status} | {scenario}")
    if details:
        for line in details.split('\n'):
            print(f"         {line}")


def test_scenario_a_low_profit_veto():
    """(a) Low-profit trade that fails after Brokerage/Taxes/Slippage -> VETOED with exact math!"""
    print_header("SCENARIO (a): Low-Profit Trade Vetoed by Cost Guard")

    # Shoonya Nifty Futures - tiny profit that gets eaten by costs
    # Entry: 19500, SL: 19490 (risk 10), Target: 19515 (reward 15) -> Gross RR 1.5
    # Quantity: 50 (1 lot = 50), Lot size: 50
    # Brokerage: 20*2 = 40, STT: 19500*50*0.025% = 243.75
    # Exchange: 19500*50*0.00345% = 33.64, GST: (40+33.64)*18% = 13.25
    # Stamp: 19500*50*0.003% = 29.25, Slippage: 19500*50*5bps*2 = 97.50
    # Total costs: 457.39, Gross reward: 15*50 = 750, Net: 292.61 (still viable)

    # Make it even tighter: Entry 19500, SL 19495 (risk 5), Target 19505 (reward 5) -> Gross RR 1.0
    # But need min RR 2 for entry... Let's try SL 19490 (10), Target 19505 (5) -> invalid
    # Try: Entry 19500, SL 19498 (2), Target 19506 (6) -> RR 3.0 but tiny
    # Gross reward: 6*50 = 300, Gross risk: 2*50 = 100
    # Costs: ~457 -> Net reward = -157 -> VETOED

    result = CostCalculator.calculate_shoonya_costs(
        side="BUY",
        entry_price=19500.0,
        stop_loss=19498.0,
        take_profit=19506.0,
        quantity=50,
        lot_size=50,
        segment=SegmentType.FUTURES,
    )

    print(f"  Entry: 19500, SL: 19498, Target: 19506, Qty: 50 (1 lot Nifty)")
    print(f"  Gross Reward: ₹{result.gross_reward:.2f}")
    print(f"  Gross Risk: ₹{result.gross_risk:.2f}")
    print(f"  Cost Breakdown:")
    print(f"    Brokerage: ₹{result.brokerage:.2f}")
    print(f"    STT: ₹{result.stt:.2f}")
    print(f"    Exchange Txn: ₹{result.exchange_txn:.2f}")
    print(f"    GST: ₹{result.gst:.2f}")
    print(f"    Stamp Duty: ₹{result.stamp_duty:.2f}")
    print(f"    Slippage Buffer: ₹{result.slippage_buffer:.2f}")
    print(f"  Total Costs: ₹{result.total_costs:.2f}")
    print(f"  Net Reward: ₹{result.net_reward:.2f}")
    print(f"  Net Risk: ₹{result.net_risk:.2f}")
    print(f"  Net R:R: {result.net_rr_ratio:.2f}")
    print(f"  Viable: {result.is_viable}")
    print(f"  Veto Reason: {result.veto_reason}")

    passed = (
        not result.is_viable
        and result.net_reward < 0
        and "NET_PROFIT_NON_POSITIVE" in (result.veto_reason or "")
    )
    print_result("(a) Low-profit veto", passed, f"Net reward: ₹{result.net_reward:.2f}, Veto: {result.veto_reason}")
    return passed


def test_scenario_b_wide_spread_fake_breakout():
    """(b) Wide-spread / fake breakout trap even with high signal score -> VETOED!"""
    print_header("SCENARIO (b): Risk Veto Shield - Wide Spread & Fake Breakout")

    # Test 1: Wide Spread Veto (spread > 0.5%)
    print("\n  Test 1: Wide Spread Veto")
    veto1 = CostCalculator.check_risk_veto(
        bid=19400.0,
        ask=19600.0,  # 200 point spread on 19500 = 1.02% > 0.5%
        last_price=19500.0,
        volume_24h=100000,
        avg_volume_24h=100000,
        high_24h=19550.0,
        low_24h=19450.0,
        open_price=19500.0,
    )
    print(f"    Bid: 19400, Ask: 19600, Spread: {veto1.spread_pct:.2f}%")
    print(f"    Vetoed: {veto1.is_vetoed}, Reason: {veto1.reason}")

    # Test 2: Low Liquidity Veto
    print("\n  Test 2: Low Liquidity Veto")
    veto2 = CostCalculator.check_risk_veto(
        bid=19499.0,
        ask=19501.0,  # Tight spread
        last_price=19500.0,
        volume_24h=1000,  # Very low volume
        avg_volume_24h=100000,  # 100x average
        high_24h=19550.0,
        low_24h=19450.0,
        open_price=19500.0,
    )
    print(f"    Volume: 1000, Avg Volume: 100000, Liquidity Score: {veto2.liquidity_score:.1f}")
    print(f"    Vetoed: {veto2.is_vetoed}, Reason: {veto2.reason}")

    # Test 3: Fake Breakout Veto (upper wick rejection without volume)
    print("\n  Test 3: Fake Breakout Veto (Wick Rejection)")
    veto3 = CostCalculator.check_risk_veto(
        bid=19499.0,
        ask=19501.0,
        last_price=19502.0,  # Closed near high
        volume_24h=20000,  # Low volume (0.2x avg)
        avg_volume_24h=100000,
        high_24h=19580.0,  # High wick
        low_24h=19480.0,
        open_price=19500.0,  # Open = close approx
    )
    print(f"    High: 19580, Low: 19480, Open: 19500, Close: 19502")
    print(f"    Upper wick: 78, Range: 100, Wick ratio: 78%")
    print(f"    Volume ratio: 0.2x")
    print(f"    Vetoed: {veto3.is_vetoed}, Reason: {veto3.reason}")
    print(f"    Fake breakout detected: {veto3.fake_breakout_detected}")

    # Test 4: Healthy setup (should pass)
    print("\n  Test 4: Healthy Setup (Should PASS)")
    veto4 = CostCalculator.check_risk_veto(
        bid=19499.5,
        ask=19500.5,  # 1 point spread = 0.005%
        last_price=19500.0,
        volume_24h=150000,  # Above average
        avg_volume_24h=100000,
        high_24h=19520.0,
        low_24h=19480.0,
        open_price=19490.0,  # Bullish candle
    )
    print(f"    Spread: {veto4.spread_pct:.3f}%, Liquidity: {veto4.liquidity_score:.1f}")
    print(f"    Vetoed: {veto4.is_vetoed}, Reason: {veto4.reason}")

    passed = (
        veto1.is_vetoed and "WIDE_SPREAD" in veto1.reason
        and veto2.is_vetoed and "LOW_LIQUIDITY" in veto2.reason
        and veto3.is_vetoed and "FAKE_BREAKOUT" in veto3.reason
        and not veto4.is_vetoed
    )
    print_result("(b) Risk Veto Shield", passed)
    return passed


def test_scenario_c_duplicate_order():
    """(c) Duplicate order attempt for same symbol/side -> BLOCKED!"""
    print_header("SCENARIO (c): Duplicate Order Prevention")

    guardian = RiskGuardian(max_daily_loss_amount=100000, max_weekly_loss_amount=500000)
    guardian.configure("test-user", 500000, 5000, 20000, 10, 2.0)

    # First order - healthy setup that passes cost guard (net RR > 1.5)
    # Entry: 19500, SL: 19450 (risk 50), Target: 19650 (reward 150) -> RR 3.0
    # Qty: 50, Gross reward: 7500, Gross risk: 2500
    # Risk per trade: 2500, Capital: 500000, 2% cap = 10000 -> OK
    # Costs ~945, Net reward: ~6555, Net risk: ~3445, Net RR: ~1.90 > 1.5
    allowed1, reason1, state1 = guardian.precheck_entry(
        subject="test-user",
        entry_price=19500.0,
        stop_loss=19450.0,
        quantity=50,
        is_auto_trade=False,
        side="BUY",
        symbol="NIFTY",
        broker="SHOONYA",
        exchange="NFO",
        take_profit=19650.0,
        bid=19499.5,
        ask=19500.5,
        last_price=19500.0,
        volume_24h=150000,
        avg_volume_24h=100000,
        high_24h=19520.0,
        low_24h=19480.0,
        open_price=19490.0,
    )
    print(f"  Order 1: BUY NIFTY @19500, SL:19450, TP:19650")
    print(f"    Allowed: {allowed1}, Reason: {reason1}")

    # Immediate duplicate (within 5 seconds) - should be blocked
    allowed2, reason2, state2 = guardian.precheck_entry(
        subject="test-user",
        entry_price=19500.0,
        stop_loss=19450.0,
        quantity=50,
        is_auto_trade=False,
        side="BUY",
        symbol="NIFTY",
        broker="SHOONYA",
        exchange="NFO",
        take_profit=19650.0,
        bid=19499.5,
        ask=19500.5,
        last_price=19500.0,
        volume_24h=150000,
        avg_volume_24h=100000,
        high_24h=19520.0,
        low_24h=19480.0,
        open_price=19490.0,
    )
    print(f"  Order 2: BUY NIFTY @19500 (immediate duplicate)")
    print(f"    Allowed: {allowed2}, Reason: {reason2}")

    # Different side - should pass
    allowed3, reason3, state3 = guardian.precheck_entry(
        subject="test-user",
        entry_price=19500.0,
        stop_loss=19550.0,
        quantity=50,
        is_auto_trade=False,
        side="SELL",
        symbol="NIFTY",
        broker="SHOONYA",
        exchange="NFO",
        take_profit=19350.0,
        bid=19499.5,
        ask=19500.5,
        last_price=19500.0,
        volume_24h=150000,
        avg_volume_24h=100000,
        high_24h=19520.0,
        low_24h=19480.0,
        open_price=19490.0,
    )
    print(f"  Order 3: SELL NIFTY @19500 (different side)")
    print(f"    Allowed: {allowed3}, Reason: {reason3}")

    passed = (
        allowed1 and reason1 == "OK"
        and not allowed2 and "DUPLICATE_ORDER_VETO" in reason2
        and allowed3 and reason3 == "OK"
    )
    print_result("(c) Duplicate Order Block", passed, f"Order1: {reason1}, Order2: {reason2}, Order3: {reason3}")
    return passed


def test_scenario_d_weekly_loss_limit():
    """(d) Weekly loss limit exceeded -> BLOCKED!"""
    print_header("SCENARIO (d): Weekly Loss Limit Exceeded")

    guardian = RiskGuardian(max_daily_loss_amount=5000, max_weekly_loss_amount=15000)
    guardian.configure("weekly-test-user", 100000, 5000, 15000, 10, 2.0)

    # Simulate daily losses within daily limit but exceeding weekly
    # Day 1: -3000 (within daily 5000, weekly 15000)
    state = guardian.sync_pnl("weekly-test-user", -3000, -3000)
    print(f"  Day 1: Daily P&L: -3000, Weekly P&L: -3000")
    print(f"    Hard locked: {state.is_hard_locked}, Reason: {state.locked_reason}")

    # Day 2: -3000 (cumulative -6000)
    state = guardian.sync_pnl("weekly-test-user", -3000, -6000)
    print(f"  Day 2: Daily P&L: -3000, Weekly P&L: -6000")
    print(f"    Hard locked: {state.is_hard_locked}, Reason: {state.locked_reason}")

    # Day 3: -3000 (cumulative -9000)
    state = guardian.sync_pnl("weekly-test-user", -3000, -9000)
    print(f"  Day 3: Daily P&L: -3000, Weekly P&L: -9000")
    print(f"    Hard locked: {state.is_hard_locked}, Reason: {state.locked_reason}")

    # Day 4: -3000 (cumulative -12000)
    state = guardian.sync_pnl("weekly-test-user", -3000, -12000)
    print(f"  Day 4: Daily P&L: -3000, Weekly P&L: -12000")
    print(f"    Hard locked: {state.is_hard_locked}, Reason: {state.locked_reason}")

    # Day 5: -4000 (cumulative -16000 > weekly limit 15000) -> SHOULD BLOCK
    state = guardian.sync_pnl("weekly-test-user", -4000, -16000)
    print(f"  Day 5: Daily P&L: -4000, Weekly P&L: -16000 (EXCEEDS 15000)")
    print(f"    Hard locked: {state.is_hard_locked}, Reason: {state.locked_reason}")

    # Try to place order after weekly limit breached
    allowed, reason, _ = guardian.precheck_entry(
        subject="weekly-test-user",
        entry_price=19500.0,
        stop_loss=19480.0,
        quantity=50,
        is_auto_trade=False,
        side="BUY",
    )
    print(f"  Order attempt after weekly breach:")
    print(f"    Allowed: {allowed}, Reason: {reason}")

    passed = (
        state.is_hard_locked
        and state.locked_reason == "WEEKLY_LOSS_LIMIT_BREACHED"
        and not allowed
        and "WEEKLY_LOSS_LIMIT_BREACHED" in reason
    )
    print_result("(d) Weekly Loss Limit", passed, f"Locked: {state.is_hard_locked}, Reason: {state.locked_reason}, Order blocked: {not allowed}")
    return passed


def test_scenario_e_healthy_net_profitable():
    """(e) Healthy net-profitable setup -> APPROVED!"""
    print_header("SCENARIO (e): Healthy Net-Profitable Setup Approved")

    # Shoonya Nifty Futures - healthy setup with higher RR
    # Entry: 19500, SL: 19450 (risk 50), Target: 19650 (reward 150) -> Gross RR 3.0
    # Quantity: 50 (1 lot), Lot size: 50
    # Gross reward: 150*50 = 7500, Gross risk: 50*50 = 2500
    # Costs: Brokerage 40, STT ~244, Exchange 34, GST 13, Stamp 29, Slippage 98 = ~458
    # Net reward: 7500 - 458 = 7042, Net risk: 2500 + 458 = 2958
    # Net RR: 7042/2958 = 2.38 > 1.5 threshold -> VIABLE

    result = CostCalculator.calculate_shoonya_costs(
        side="BUY",
        entry_price=19500.0,
        stop_loss=19450.0,
        take_profit=19650.0,
        quantity=50,
        lot_size=50,
        segment=SegmentType.FUTURES,
    )

    print(f"  Entry: 19500, SL: 19450, Target: 19650, Qty: 50 (1 lot Nifty)")
    print(f"  Gross Reward: ₹{result.gross_reward:.2f}")
    print(f"  Gross Risk: ₹{result.gross_risk:.2f}")
    print(f"  Total Costs: ₹{result.total_costs:.2f}")
    print(f"  Net Reward: ₹{result.net_reward:.2f}")
    print(f"  Net Risk: ₹{result.net_risk:.2f}")
    print(f"  Net R:R: {result.net_rr_ratio:.2f}")
    print(f"  Viable: {result.is_viable}")

    # Also test with RiskGuardian precheck - use higher capital for per-trade risk cap
    guardian = RiskGuardian(max_daily_loss_amount=10000, max_weekly_loss_amount=50000)
    guardian.configure("healthy-user", 500000, 10000, 50000, 10, 2.0)

    allowed, reason, state = guardian.precheck_entry(
        subject="healthy-user",
        entry_price=19500.0,
        stop_loss=19450.0,
        quantity=50,
        is_auto_trade=False,
        side="BUY",
        broker="SHOONYA",
        symbol="NIFTY",
        exchange="NFO",
        take_profit=19650.0,
        bid=19499.5,
        ask=19500.5,
        last_price=19500.0,
        volume_24h=150000,
        avg_volume_24h=100000,
        high_24h=19520.0,
        low_24h=19480.0,
        open_price=19490.0,
    )

    print(f"\n  RiskGuardian Precheck:")
    print(f"    Allowed: {allowed}, Reason: {reason}")
    print(f"    Trades count: {state.trades_count}, Weekly trades: {state.weekly_trades_count}")

    passed = (
        result.is_viable
        and result.net_reward > 0
        and result.net_rr_ratio >= 1.5
        and allowed
        and reason == "OK"
    )
    print_result("(e) Healthy Setup Approved", passed, f"Net RR: {result.net_rr_ratio:.2f}, Net Profit: ₹{result.net_reward:.2f}, Allowed: {allowed}")
    return passed


def test_coindcx_cost_calculation():
    """Bonus: Test CoinDCX cost calculation for crypto futures"""
    print_header("BONUS: CoinDCX Crypto Futures Cost Calculation")

    # BTCUSDT: Entry 65000, SL 64500 (risk 500), Target 66000 (reward 1000) -> RR 2.0
    # Qty: 0.1 BTC, FX: 86 INR/USDT
    # Gross risk: 500 * 0.1 * 86 = 4300 INR
    # Gross reward: 1000 * 0.1 * 86 = 8600 INR
    # Turnover: 65000 * 0.1 = 6500 USDT
    # Trading fee: 6500 * 0.05% * 2 = 6.5 USDT = 559 INR
    # TDS: 6500 * 1% = 65 USDT = 5590 INR
    # GST: 6.5 * 18% = 1.17 USDT = 100.62 INR
    # Slippage: 6500 * 2bps * 2 = 2.6 USDT = 223.6 INR
    # Total costs: 559 + 5590 + 101 + 224 = 6474 INR
    # Net reward: 8600 - 6474 = 2126 INR
    # Net risk: 4300 + 6474 = 10774 INR
    # Net RR: 2126/10774 = 0.20 -> BELOW THRESHOLD!

    result = CostCalculator.calculate_coindcx_costs(
        side="BUY",
        entry_price=65000.0,
        stop_loss=64500.0,
        take_profit=66000.0,
        quantity=0.1,
        fx_to_inr=86.0,
    )

    print(f"  Entry: 65000, SL: 64500, Target: 66000, Qty: 0.1 BTC, FX: 86")
    print(f"  Gross Reward: ₹{result.gross_reward:.2f}")
    print(f"  Gross Risk: ₹{result.gross_risk:.2f}")
    print(f"  Trading Fee: ₹{result.brokerage:.2f}")
    print(f"  TDS: ₹{result.tds:.2f}")
    print(f"  GST: ₹{result.gst:.2f}")
    print(f"  Slippage: ₹{result.slippage_buffer:.2f}")
    print(f"  Total Costs: ₹{result.total_costs:.2f}")
    print(f"  Net Reward: ₹{result.net_reward:.2f}")
    print(f"  Net Risk: ₹{result.net_risk:.2f}")
    print(f"  Net R:R: {result.net_rr_ratio:.2f}")
    print(f"  Viable: {result.is_viable}")
    print(f"  Veto Reason: {result.veto_reason}")

    # With current parameters, TDS makes this non-viable
    # This is expected behavior - TDS makes high-frequency crypto costly
    passed = (
        result.gross_reward == 8600.0
        and result.gross_risk == 4300.0
        and abs(result.tds - 5590.0) < 1  # 65 USDT * 86 = 5590
        and abs(result.net_rr_ratio - 0.20) < 0.05
    )
    print_result("CoinDCX Cost Calc", passed, f"Net RR: {result.net_rr_ratio:.2f}, Veto: {result.veto_reason}")
    return passed


def main():
    print("\n" + "🔥" * 35)
    print("  MODULE E7 - CAPITAL PROTECTION VERIFICATION")
    print("  Net-Profit Cost Guard | Risk Veto Shield | Weekly Loss Limit | Duplicate Prevention")
    print("🔥" * 35)

    results = []

    results.append(("Scenario (a): Low-Profit Veto", test_scenario_a_low_profit_veto()))
    results.append(("Scenario (b): Risk Veto Shield", test_scenario_b_wide_spread_fake_breakout()))
    results.append(("Scenario (c): Duplicate Order Block", test_scenario_c_duplicate_order()))
    results.append(("Scenario (d): Weekly Loss Limit", test_scenario_d_weekly_loss_limit()))
    results.append(("Scenario (e): Healthy Setup Approved", test_scenario_e_healthy_net_profitable()))
    results.append(("Bonus: CoinDCX Cost Calc", test_coindcx_cost_calculation()))

    print("\n" + "="*70)
    print("  SUMMARY")
    print("="*70)
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} | {name}")
        if not passed:
            all_passed = False

    print("="*70)
    if all_passed:
        print("  🎉 ALL 5+1 SCENARIOS PASSED - Module E7 Capital Protection VERIFIED!")
    else:
        print("  ⚠️  SOME TESTS FAILED - Review implementation")
    print("="*70)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
from backend.app.services.apex_predator import detect_retail_trap, evaluate_apex_setup


def _bar(index, open_price, high, low, close, *, closed=True):
    return {
        "time": index,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "is_closed": closed,
    }


def _base_bars():
    bars = [
        _bar(index, 100, 101, 99, 100)
        for index in range(10)
    ]
    bars[2] = _bar(2, 100, 105, 99, 103)
    bars[6] = _bar(6, 102, 105, 100, 101)
    return bars


def test_equal_high_liquidity_sweep_produces_opposite_sell_signal():
    bars = _base_bars()
    bars.append(_bar(10, 104, 106, 102, 103))

    trap = detect_retail_trap(bars, lookback=10)

    assert trap is not None
    assert trap["direction"] == "SELL"
    assert trap["pattern"] == "EQUAL_HIGHS_SWEEP"
    assert trap["explanation_bn"]


def test_equal_low_liquidity_sweep_produces_opposite_buy_signal():
    bars = [_bar(index, 100, 101, 99, 100) for index in range(10)]
    bars[2] = _bar(2, 100, 101, 95, 97)
    bars[6] = _bar(6, 99, 100, 95, 98)
    bars.append(_bar(10, 96, 98, 94, 97))

    trap = detect_retail_trap(bars, lookback=10)

    assert trap is not None
    assert trap["direction"] == "BUY"
    assert trap["pattern"] == "EQUAL_LOWS_SWEEP"


def test_unclosed_bar_cannot_trigger_a_trap():
    bars = _base_bars()
    bars.append(_bar(10, 104, 106, 102, 103, closed=False))

    assert detect_retail_trap(bars, lookback=10) is None


def test_failed_descending_trendline_break_produces_opposite_sell_signal():
    bars = [_bar(index, 100, 101, 99, 100) for index in range(30)]
    bars[5] = _bar(5, 100, 111, 99, 101)
    bars[15] = _bar(15, 100, 106, 99, 101)
    bars.append(_bar(30, 101, 103, 97, 98))

    trap = detect_retail_trap(bars)

    assert trap is not None
    assert trap["pattern"] == "FAILED_DESCENDING_TRENDLINE_BREAK"
    assert trap["direction"] == "SELL"


def test_apex_gate_requires_70_all_four_inputs_and_aligned_direction():
    gates = {"smc": True, "volume": True, "news": True, "closed_candles": True}
    aligned = {"1m": "SELL", "5m": "SELL", "1h": "SELL"}

    passed = evaluate_apex_setup(
        score=70, side="SELL", timeframe_sides=aligned, required_gates=gates
    )
    assert passed["allowed"] is True
    assert passed["side"] == "SELL"

    too_low = evaluate_apex_setup(
        score=69, side="SELL", timeframe_sides=aligned, required_gates=gates
    )
    assert "CONFLUENCE_BELOW_70" in too_low["reasons"]

    blocked = evaluate_apex_setup(
        score=95,
        side="SELL",
        timeframe_sides={**aligned, "1m": "BUY"},
        required_gates={**gates, "news": False},
    )
    assert blocked["allowed"] is False
    assert "MULTI_TIMEFRAME_NOT_ALIGNED" in blocked["reasons"]
    assert "GATE_FAILED:news" in blocked["reasons"]


def test_trap_direction_only_overrides_base_signal_when_mtf_confirms_it():
    gates = {"smc": True, "volume": True, "news": True, "closed_candles": True}
    trap = {"direction": "SELL", "pattern": "EQUAL_HIGHS_SWEEP"}

    result = evaluate_apex_setup(
        score=88,
        side="BUY",
        timeframe_sides={"1m": "SELL", "5m": "SELL", "1h": "SELL"},
        required_gates=gates,
        trap=trap,
    )
    assert result["allowed"] is True
    assert result["side"] == "SELL"

    rejected = evaluate_apex_setup(
        score=88,
        side="BUY",
        timeframe_sides={"1m": "BUY", "5m": "BUY", "1h": "BUY"},
        required_gates=gates,
        trap=trap,
    )
    assert rejected["allowed"] is False
    assert "TRAP_DIRECTION_NOT_CONFIRMED_BY_MTF" in rejected["reasons"]

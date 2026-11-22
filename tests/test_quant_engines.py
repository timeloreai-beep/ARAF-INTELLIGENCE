import asyncio

from backend.app.quant.engines import ENGINE_METADATA, evaluate_engine_suite
from backend.app.quant.indicators import atr, rsi, supertrend
from backend.app.quant.smc import bos_choch, order_blocks
from backend.app.routers.engines import evaluate_engines
from backend.app.schemas import EngineEvaluationRequest


def _bar(index, open_price, high, low, close):
    return {
        "time": index,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "is_closed": True,
    }


def test_indicator_wilder_edges_and_supertrend_direction():
    assert rsi([100, 100, 100, 100], period=3) == 50
    assert rsi([1, 2, 3, 4], period=3) == 100

    candles = [_bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(5)]
    assert atr(candles, period=3) == 3
    breakout = candles + [_bar(5, 105, 109, 104, 108)]
    assert supertrend(breakout, period=3, multiplier=1)["direction"] == "BUY"


def test_smc_order_block_requires_displacement_and_structure_change():
    candles = [
        _bar(0, 10.0, 10.4, 9.8, 10.2),
        _bar(1, 10.2, 10.5, 10.0, 10.3),
        _bar(2, 10.3, 10.6, 10.1, 10.4),
        _bar(3, 10.4, 10.6, 10.2, 10.3),
        _bar(4, 10.3, 10.4, 9.9, 10.0),
        _bar(5, 10.0, 12.0, 9.9, 11.8),
    ]
    blocks = order_blocks(candles)
    assert len(blocks) == 1
    assert blocks[0]["side"] == "BULLISH"
    assert blocks[0]["time"] == 4

    structure = [
        _bar(0, 10, 11, 9, 10),
        _bar(1, 10, 12, 9.5, 11),
        _bar(2, 11, 15, 10, 13),
        _bar(3, 10, 12, 8, 9),
        _bar(4, 9, 13, 9, 12),
        _bar(5, 12, 14, 9.5, 10),
        _bar(6, 10, 12, 7, 9),
        _bar(7, 9, 13, 8, 11),
        _bar(8, 11, 11.5, 8.5, 10),
        _bar(9, 10, 12, 9, 11),
        _bar(10, 14.5, 16, 14, 15.5),
    ]
    assert bos_choch(structure)["structure"] == "CHoCH"
    assert bos_choch(structure)["side"] == "BUY"


def test_twenty_engine_suite_is_explicitly_fail_closed_without_required_inputs():
    payload = {
        "symbol": "TEST",
        "order_key": "TEST",
        "category": "indian",
        "base_side": "BUY",
        "base_score": 95,
        "ltp": 100,
        "sl": 99,
        "data_quality": "LIVE",
        "event_time_ms": None,
    }
    report = evaluate_engine_suite(payload)
    assert len(report["engines"]) == len(ENGINE_METADATA) == 20
    assert report["autoTradeEligible"] is False
    by_id = {engine["engineId"]: engine for engine in report["engines"]}
    assert by_id["ENG05_TAPE_READING"]["status"] == "UNAVAILABLE"
    assert by_id["ENG07_OPTIONS_FLOW"]["status"] == "NOT_APPLICABLE"
    assert by_id["ENG10_EVENT_CATALYST"]["status"] == "UNAVAILABLE"
    assert by_id["ENG20_FEED_CONSENSUS"]["status"] == "FAIL"


def test_sizing_never_rounds_up_to_a_risk_exceeding_lot():
    report = evaluate_engine_suite({
        "symbol": "TEST",
        "order_key": "TEST",
        "category": "indian",
        "base_side": "BUY",
        "ltp": 100,
        "sl": 99,
        "execution": {
            "broker": "SHOONYA",
            "segment": "EQ",
            "lot_size": 10,
        },
        "sizing": {
            "daily_budget_inr": 100,
            "capital_inr": 10000,
        },
    })
    sizing = next(engine for engine in report["engines"] if engine["engineId"] == "ENG16_ADAPTIVE_SIZING")
    assert sizing["status"] == "FAIL"
    assert report["sizing"]["qty"] == 0


def test_authenticated_api_handler_returns_quant_features_without_execution():
    payload = EngineEvaluationRequest(
        symbol="TEST",
        order_key="TEST",
        candles=[_bar(0, 10, 11, 9, 10)],
    )
    report = asyncio.run(evaluate_engines(payload, subject="test-user"))
    assert report["analysisOnly"] is True
    assert report["executionEnabled"] is False
    assert report["quant_features"]["closed_candle_count"] == 1
    assert report["quant_features"]["indicators"]["volume_imbalance"]["status"] == "UNAVAILABLE"

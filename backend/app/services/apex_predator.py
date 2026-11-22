from __future__ import annotations

import math
from typing import Any


_REQUIRED_TIMEFRAMES = ("1m", "5m", "1h")
_REQUIRED_GATES = {"smc", "volume", "news", "closed_candles"}


def detect_retail_trap(
    candles: list[dict[str, Any]],
    *,
    lookback: int = 30,
    tolerance_pct: float = 0.001,
) -> dict[str, Any] | None:
    """Detect confirmed stop sweeps and failed range/trendline breaks on closed bars."""
    closed = [bar for bar in candles if bar.get("is_closed") is True]
    if len(closed) < max(8, lookback + 1):
        return None
    window, current = closed[-lookback - 1:-1], closed[-1]
    numeric_keys = ("open", "high", "low", "close")
    try:
        rows = [
            {key: float(bar[key]) for key in numeric_keys}
            for bar in [*window, current]
        ]
    except (KeyError, TypeError, ValueError):
        return None
    if any(
        not all(math.isfinite(value) and value > 0 for value in bar.values())
        or bar["low"] > min(bar["open"], bar["close"])
        or bar["high"] < max(bar["open"], bar["close"])
        or bar["low"] > bar["high"]
        for bar in rows
    ):
        return None

    prior = rows[:-1]
    last = rows[-1]
    avg_range = sum(bar["high"] - bar["low"] for bar in prior[-14:]) / min(14, len(prior))
    tolerance = max(last["close"] * tolerance_pct, avg_range * 0.08)
    candidates: list[tuple[float, dict[str, Any]]] = []

    def add(direction: str, pattern: str, level: float, excursion: float) -> None:
        candidates.append((
            excursion / max(tolerance, 1e-12),
            {
                "direction": direction,
                "pattern": pattern,
                "level": level,
                "explanation_bn": (
                    "সমান উচ্চতার লিকুইডিটি sweep করে দাম নিচে বন্ধ হয়েছে; সম্ভাব্য bearish trap।"
                    if direction == "SELL"
                    else "সমান নিম্নের লিকুইডিটি sweep করে দাম ওপরে বন্ধ হয়েছে; সম্ভাব্য bullish trap।"
                ),
            },
        ))

    prior_highs = [bar["high"] for bar in prior]
    prior_lows = [bar["low"] for bar in prior]

    equal_highs = [
        (left + right) / 2
        for index, left in enumerate(prior_highs)
        for right in prior_highs[index + 1:]
        if abs(left - right) <= tolerance
    ]
    equal_lows = [
        (left + right) / 2
        for index, left in enumerate(prior_lows)
        for right in prior_lows[index + 1:]
        if abs(left - right) <= tolerance
    ]
    for level in equal_highs:
        excursion = last["high"] - level
        if excursion > tolerance and last["close"] < level and last["close"] < last["open"]:
            add("SELL", "EQUAL_HIGHS_SWEEP", level, excursion)
    for level in equal_lows:
        excursion = level - last["low"]
        if excursion > tolerance and last["close"] > level and last["close"] > last["open"]:
            add("BUY", "EQUAL_LOWS_SWEEP", level, excursion)

    range_high, range_low = max(prior_highs), min(prior_lows)
    if last["high"] > range_high + tolerance and last["close"] < range_high:
        add("SELL", "FAILED_RESISTANCE_BREAKOUT", range_high, last["high"] - range_high)
    if last["low"] < range_low - tolerance and last["close"] > range_low:
        add("BUY", "FAILED_SUPPORT_BREAKDOWN", range_low, range_low - last["low"])

    pivots_high = [
        (index, bar["high"])
        for index, bar in enumerate(prior)
        if 0 < index < len(prior) - 1
        and bar["high"] >= prior[index - 1]["high"]
        and bar["high"] > prior[index + 1]["high"]
    ]
    pivots_low = [
        (index, bar["low"])
        for index, bar in enumerate(prior)
        if 0 < index < len(prior) - 1
        and bar["low"] <= prior[index - 1]["low"]
        and bar["low"] < prior[index + 1]["low"]
    ]
    if len(pivots_high) >= 2:
        (x1, y1), (x2, y2) = pivots_high[-2:]
        if x2 > x1 and y2 < y1:
            line = y2 + (y2 - y1) / (x2 - x1) * (len(prior) - 1 - x2)
            if last["high"] > line + tolerance and last["close"] < line:
                add("SELL", "FAILED_DESCENDING_TRENDLINE_BREAK", line, last["high"] - line)
    if len(pivots_low) >= 2:
        (x1, y1), (x2, y2) = pivots_low[-2:]
        if x2 > x1 and y2 > y1:
            line = y2 + (y2 - y1) / (x2 - x1) * (len(prior) - 1 - x2)
            if last["low"] < line - tolerance and last["close"] > line:
                add("BUY", "FAILED_ASCENDING_TRENDLINE_BREAK", line, line - last["low"])

    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    if len(candidates) > 1 and candidates[0][1]["direction"] != candidates[1][1]["direction"]:
        if math.isclose(candidates[0][0], candidates[1][0], rel_tol=0.1):
            return None
    return candidates[0][1]


def volume_profile_confirmation(
    candles: list[dict[str, Any]],
    side: str,
    *,
    bins: int = 12,
) -> dict[str, Any]:
    bars = [bar for bar in candles if bar.get("is_closed") is True]
    if side not in {"BUY", "SELL"} or len(bars) < 20 or bins < 2:
        return {"passed": False, "reason": "INSUFFICIENT_CLOSED_VOLUME_DATA"}
    try:
        low = min(float(bar["low"]) for bar in bars)
        high = max(float(bar["high"]) for bar in bars)
        volumes = [float(bar["volume"]) for bar in bars]
        closes = [float(bar["close"]) for bar in bars]
    except (KeyError, TypeError, ValueError):
        return {"passed": False, "reason": "INVALID_VOLUME_PROFILE_DATA"}
    if (
        not all(math.isfinite(value) for value in [low, high, *volumes, *closes])
        or low <= 0
        or high < low
        or any(volume < 0 for volume in volumes)
        or sum(volumes) <= 0
    ):
        return {"passed": False, "reason": "INVALID_VOLUME_PROFILE_DATA"}

    step = (high - low) / bins if high > low else max(high * 1e-6, 1e-9)
    profile = [0.0] * bins
    for bar, volume in zip(bars, volumes):
        bar_low = min(bins - 1, max(0, math.floor((float(bar["low"]) - low) / step)))
        bar_high = min(bins - 1, max(0, math.floor((float(bar["high"]) - low) / step)))
        spread = bar_high - bar_low + 1
        for index in range(bar_low, bar_high + 1):
            profile[index] += volume / spread
    poc_index = max(range(bins), key=profile.__getitem__)
    poc = low + (poc_index + 0.5) * step
    close = closes[-1]
    return {
        "passed": close >= poc if side == "BUY" else close <= poc,
        "poc": poc,
        "last_close": close,
        "reason": "PRICE_ACCEPTED_ABOVE_POC" if side == "BUY" and close >= poc
        else "PRICE_ACCEPTED_BELOW_POC" if side == "SELL" and close <= poc
        else "PRICE_NOT_ACCEPTED_ON_DIRECTIONAL_POC_SIDE",
    }


def evaluate_apex_setup(
    *,
    score: int,
    side: str,
    timeframe_sides: dict[str, str],
    required_gates: dict[str, bool],
    trap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed, direction-aware 70-point execution decision."""
    reasons: list[str] = []
    aligned = all(
        timeframe_sides.get(timeframe) in {"BUY", "SELL"}
        for timeframe in _REQUIRED_TIMEFRAMES
    )
    directions = {timeframe_sides.get(timeframe) for timeframe in _REQUIRED_TIMEFRAMES}
    if not aligned or len(directions) != 1:
        reasons.append("MULTI_TIMEFRAME_NOT_ALIGNED")
    direction = next(iter(directions)) if len(directions) == 1 else "NEUTRAL"
    has_trap_direction = bool(trap and trap.get("direction") in {"BUY", "SELL"})
    if has_trap_direction:
        if direction == trap["direction"]:
            direction = trap["direction"]
        else:
            reasons.append("TRAP_DIRECTION_NOT_CONFIRMED_BY_MTF")
    elif side in {"BUY", "SELL"}:
        if direction == side:
            direction = side
        else:
            reasons.append("SIGNAL_DIRECTION_NOT_CONFIRMED_BY_MTF")
    if side not in {"BUY", "SELL"} and not has_trap_direction:
        reasons.append("NO_DIRECTIONAL_SIGNAL")
    if score < 70:
        reasons.append("CONFLUENCE_BELOW_70")
    missing_gates = sorted(
        name for name in _REQUIRED_GATES
        if required_gates.get(name) is not True
    )
    reasons.extend(f"GATE_FAILED:{name}" for name in missing_gates)
    return {
        "allowed": not reasons,
        "side": direction,
        "score": score,
        "trap": trap,
        "reasons": reasons,
    }

from __future__ import annotations

import math
from typing import Iterable


def _validated_bars(candles: Iterable[dict]) -> list[dict]:
    bars = list(candles)
    for bar in bars:
        values = [float(bar[key]) for key in ("open", "high", "low", "close")]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("candles must contain finite OHLC values")
        open_price, high, low, close = values
        if low <= 0 or high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError("candles must contain valid OHLC ranges")
    return bars


def fair_value_gaps(candles: Iterable[dict], limit: int = 20) -> list[dict]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return []
    bars = _validated_bars(candles)
    gaps: list[dict] = []
    for i in range(2, len(bars)):
        current = bars[i]
        prior = bars[i - 2]
        if float(prior["low"]) > float(current["high"]):
            gaps.append({"side": "BEARISH", "top": float(prior["low"]), "bottom": float(current["high"]), "time": current["time"]})
        elif float(prior["high"]) < float(current["low"]):
            gaps.append({"side": "BULLISH", "bottom": float(prior["high"]), "top": float(current["low"]), "time": current["time"]})
    return gaps[-limit:]


def order_blocks(candles: Iterable[dict], limit: int = 20) -> list[dict]:
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if limit == 0:
        return []
    bars = _validated_bars(candles)
    blocks: list[dict] = []
    for i in range(5, len(bars)):
        current = bars[i]
        prior = bars[max(0, i - 6):i - 1]
        if not prior:
            continue
        prior_high = max(float(bar["high"]) for bar in prior)
        prior_low = min(float(bar["low"]) for bar in prior)
        prior_bodies = [
            abs(float(bar["close"]) - float(bar["open"]))
            for bar in prior
        ]
        average_body = sum(prior_bodies) / len(prior_bodies)
        body = abs(float(current["close"]) - float(current["open"]))
        if average_body <= 0 or body < average_body * 1.5:
            continue

        close = float(current["close"])
        if close > float(current["open"]) and close > prior_high:
            side = "BULLISH"
            candidates = range(i - 1, max(-1, i - 6), -1)
            candidate = next(
                (bars[j] for j in candidates if float(bars[j]["close"]) < float(bars[j]["open"])),
                None,
            )
        elif close < float(current["open"]) and close < prior_low:
            side = "BEARISH"
            candidates = range(i - 1, max(-1, i - 6), -1)
            candidate = next(
                (bars[j] for j in candidates if float(bars[j]["close"]) > float(bars[j]["open"])),
                None,
            )
        else:
            continue
        if candidate is not None:
            blocks.append({
                "side": side,
                "top": float(candidate["high"]),
                "bottom": float(candidate["low"]),
                "time": candidate["time"],
            })
    return blocks[-limit:]


def liquidity_sweeps(candles: Iterable[dict], lookback: int = 10) -> list[dict]:
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    bars = _validated_bars(candles)
    if len(bars) < lookback + 2:
        return []
    prior = bars[-lookback - 1:-1]
    previous_high = max(float(b["high"]) for b in prior)
    previous_low = min(float(b["low"]) for b in prior)
    latest = bars[-1]
    result = []
    if float(latest["high"]) > previous_high and float(latest["close"]) < previous_high:
        result.append({"type": "BUY_SIDE_SWEEP", "level": previous_high, "time": latest["time"]})
    if float(latest["low"]) < previous_low and float(latest["close"]) > previous_low:
        result.append({"type": "SELL_SIDE_SWEEP", "level": previous_low, "time": latest["time"]})
    return result


def bos_choch(candles: Iterable[dict], lookback: int = 20) -> dict:
    if lookback < 5:
        raise ValueError("lookback must be at least 5")
    bars = _validated_bars(candles)[-lookback:]
    if len(bars) < 7:
        return {"structure": "UNKNOWN", "side": "NEUTRAL", "level": None}

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for index in range(2, len(bars) - 2):
        high = float(bars[index]["high"])
        low = float(bars[index]["low"])
        if all(high > float(bars[other]["high"]) for other in (index - 2, index - 1, index + 1, index + 2)):
            swing_highs.append((index, high))
        if all(low < float(bars[other]["low"]) for other in (index - 2, index - 1, index + 1, index + 2)):
            swing_lows.append((index, low))

    if not swing_highs or not swing_lows:
        return {"structure": "RANGE", "side": "NEUTRAL", "level": None}

    latest_close = float(bars[-1]["close"])
    prior_high = swing_highs[-1][1]
    prior_low = swing_lows[-1][1]
    if len(swing_highs) > 1 and len(swing_lows) > 1:
        higher_highs = swing_highs[-1][1] > swing_highs[-2][1]
        higher_lows = swing_lows[-1][1] > swing_lows[-2][1]
        lower_highs = swing_highs[-1][1] < swing_highs[-2][1]
        lower_lows = swing_lows[-1][1] < swing_lows[-2][1]
        prior_trend = "BUY" if higher_highs and higher_lows else "SELL" if lower_highs and lower_lows else "NEUTRAL"
    else:
        prior_trend = "NEUTRAL"

    if latest_close > prior_high:
        structure = "CHoCH" if prior_trend == "SELL" else "BOS"
        return {"structure": structure, "side": "BUY", "level": prior_high}
    if latest_close < prior_low:
        structure = "CHoCH" if prior_trend == "BUY" else "BOS"
        return {"structure": structure, "side": "SELL", "level": prior_low}
    return {"structure": "RANGE", "side": "NEUTRAL", "level": None}

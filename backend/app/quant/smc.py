from __future__ import annotations

from typing import Iterable


def fair_value_gaps(candles: Iterable[dict], limit: int = 20) -> list[dict]:
    bars = list(candles)
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
    bars = list(candles)
    blocks: list[dict] = []
    for i in range(2, len(bars)):
        previous = bars[i - 1]
        current = bars[i]
        previous_bullish = float(previous["close"]) > float(previous["open"])
        current_bullish = float(current["close"]) > float(current["open"])
        if not previous_bullish and current_bullish:
            blocks.append({"side": "BULLISH", "top": float(previous["high"]), "bottom": float(previous["low"]), "time": previous["time"]})
        elif previous_bullish and not current_bullish:
            blocks.append({"side": "BEARISH", "top": float(previous["high"]), "bottom": float(previous["low"]), "time": previous["time"]})
    return blocks[-limit:]


def liquidity_sweeps(candles: Iterable[dict], lookback: int = 10) -> list[dict]:
    bars = list(candles)
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
    bars = list(candles)[-lookback:]
    if len(bars) < 5:
        return {"structure": "UNKNOWN", "side": "NEUTRAL", "level": None}
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    latest_close = float(bars[-1]["close"])
    prior_high = max(highs[:-1])
    prior_low = min(lows[:-1])
    if latest_close > prior_high:
        return {"structure": "BOS", "side": "BUY", "level": prior_high}
    if latest_close < prior_low:
        return {"structure": "BOS", "side": "SELL", "level": prior_low}
    return {"structure": "RANGE", "side": "NEUTRAL", "level": None}

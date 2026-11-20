from __future__ import annotations

import math
from typing import Iterable


def ema(values: Iterable[float], period: int) -> list[float]:
    source = [float(v) for v in values]
    if period <= 0 or not source:
        return []
    multiplier = 2 / (period + 1)
    result = [source[0]]
    for value in source[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def rsi(values: Iterable[float], period: int = 14) -> float | None:
    source = [float(v) for v in values]
    if len(source) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(source[-period - 1:-1], source[-period:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    return 100 - (100 / (1 + average_gain / average_loss))


def atr(candles: list[dict], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    true_ranges = []
    for previous, current in zip(candles[-period - 1:-1], candles[-period:]):
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(true_ranges) / period


def macd(values: Iterable[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float | None]:
    source = [float(v) for v in values]
    if len(source) < slow:
        return {"macd": None, "signal": None, "histogram": None}
    fast_ema = ema(source, fast)
    slow_ema = ema(source, slow)
    line = [f - s for f, s in zip(fast_ema[-len(slow_ema):], slow_ema)]
    if len(line) < signal:
        return {"macd": line[-1], "signal": None, "histogram": None}
    signal_line = ema(line, signal)
    macd_value = line[-1]
    signal_value = signal_line[-1]
    return {"macd": macd_value, "signal": signal_value, "histogram": macd_value - signal_value}


def bollinger_bands(values: Iterable[float], period: int = 20, standard_deviations: float = 2.0) -> dict[str, float | None]:
    source = [float(v) for v in values]
    if len(source) < period:
        return {"middle": None, "upper": None, "lower": None, "bandwidth": None}
    window = source[-period:]
    middle = sum(window) / period
    variance = sum((value - middle) ** 2 for value in window) / period
    deviation = math.sqrt(variance) * standard_deviations
    upper = middle + deviation
    lower = middle - deviation
    bandwidth = (upper - lower) / middle if middle else None
    return {"middle": middle, "upper": upper, "lower": lower, "bandwidth": bandwidth}


def supertrend(candles: list[dict], period: int = 10, multiplier: float = 3.0) -> dict:
    if len(candles) < period + 1:
        return {"value": None, "direction": "UNKNOWN"}
    atr_value = atr(candles, period)
    if atr_value is None:
        return {"value": None, "direction": "UNKNOWN"}
    last = candles[-1]
    close = float(last["close"])
    basic_upper = (float(last["high"]) + float(last["low"])) / 2 + multiplier * atr_value
    basic_lower = (float(last["high"]) + float(last["low"])) / 2 - multiplier * atr_value
    previous_close = float(candles[-2]["close"])
    direction = "BUY" if close >= previous_close else "SELL"
    value = basic_lower if direction == "BUY" else basic_upper
    return {"value": value, "direction": direction}

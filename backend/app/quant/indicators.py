from __future__ import annotations

import math
from typing import Iterable


def _finite_values(values: Iterable[float]) -> list[float]:
    source = [float(value) for value in values]
    if not all(math.isfinite(value) for value in source):
        raise ValueError("indicator inputs must be finite")
    return source


def _wilder_atr_series(candles: list[dict], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(candles) < period + 1:
        return [None] * len(candles)

    true_ranges: list[float] = []
    for previous, current in zip(candles[:-1], candles[1:]):
        high = float(current["high"])
        low = float(current["low"])
        previous_close = float(previous["close"])
        true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        if not math.isfinite(true_range) or true_range < 0:
            raise ValueError("candles must contain finite, valid OHLC values")
        true_ranges.append(true_range)

    result: list[float | None] = [None] * len(candles)
    current_atr = sum(true_ranges[:period]) / period
    result[period] = current_atr
    for index in range(period, len(true_ranges)):
        current_atr = (current_atr * (period - 1) + true_ranges[index]) / period
        result[index + 1] = current_atr
    return result


def ema(values: Iterable[float], period: int) -> list[float]:
    source = _finite_values(values)
    if period <= 0:
        raise ValueError("period must be positive")
    if not source:
        return []
    multiplier = 2 / (period + 1)
    result = [source[0]]
    for value in source[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def rsi(values: Iterable[float], period: int = 14) -> float | None:
    source = _finite_values(values)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(source) <= period:
        return None

    changes = [current - previous for previous, current in zip(source, source[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period

    if average_gain == 0 and average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    if average_gain == 0:
        return 0.0
    return 100 - (100 / (1 + average_gain / average_loss))


def atr(candles: list[dict], period: int = 14) -> float | None:
    values = _wilder_atr_series(candles, period)
    return values[-1] if values else None


def macd(values: Iterable[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float | None]:
    source = _finite_values(values)
    if min(fast, slow, signal) <= 0 or fast >= slow:
        raise ValueError("MACD periods must be positive and fast must be less than slow")
    if len(source) < slow:
        return {"macd": None, "signal": None, "histogram": None}
    fast_ema = ema(source, fast)
    slow_ema = ema(source, slow)
    line = [f - s for f, s in zip(fast_ema, slow_ema)]
    if len(line) < signal:
        return {"macd": line[-1], "signal": None, "histogram": None}
    signal_line = ema(line, signal)
    macd_value = line[-1]
    signal_value = signal_line[-1]
    return {"macd": macd_value, "signal": signal_value, "histogram": macd_value - signal_value}


def bollinger_bands(values: Iterable[float], period: int = 20, standard_deviations: float = 2.0) -> dict[str, float | None]:
    source = _finite_values(values)
    if period <= 0 or not math.isfinite(standard_deviations) or standard_deviations < 0:
        raise ValueError("period must be positive and standard_deviations must be finite and non-negative")
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


def volume_imbalance(candles: list[dict]) -> dict[str, float | str | None]:
    paired = []
    for candle in candles:
        if "buy_volume" not in candle or "sell_volume" not in candle:
            continue
        if candle["buy_volume"] is None and candle["sell_volume"] is None:
            continue
        if candle["buy_volume"] is None or candle["sell_volume"] is None:
            raise ValueError("buy_volume and sell_volume must be supplied together")
        buy_volume = float(candle["buy_volume"])
        sell_volume = float(candle["sell_volume"])
        if not math.isfinite(buy_volume) or not math.isfinite(sell_volume) or min(buy_volume, sell_volume) < 0:
            raise ValueError("buy_volume and sell_volume must be finite and non-negative")
        paired.append((buy_volume, sell_volume))
    if not paired:
        return {"buy_volume": None, "sell_volume": None, "imbalance_pct": None, "status": "UNAVAILABLE"}
    buy_total = sum(buy for buy, _ in paired)
    sell_total = sum(sell for _, sell in paired)
    total = buy_total + sell_total
    imbalance = ((buy_total - sell_total) / total * 100) if total else 0.0
    return {
        "buy_volume": buy_total,
        "sell_volume": sell_total,
        "imbalance_pct": imbalance,
        "status": "LIVE",
    }


def supertrend(candles: list[dict], period: int = 10, multiplier: float = 3.0) -> dict:
    if period <= 0 or not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("period and multiplier must be positive")
    atr_values = _wilder_atr_series(candles, period)
    if not atr_values or atr_values[-1] is None:
        return {"value": None, "direction": "UNKNOWN"}

    final_upper: float | None = None
    final_lower: float | None = None
    trend = "SELL"
    supertrend_value: float | None = None
    previous_close = float(candles[0]["close"])
    for index in range(1, len(candles)):
        current_atr = atr_values[index]
        if current_atr is None:
            previous_close = float(candles[index]["close"])
            continue
        high = float(candles[index]["high"])
        low = float(candles[index]["low"])
        close = float(candles[index]["close"])
        midpoint = (high + low) / 2
        basic_upper = midpoint + multiplier * current_atr
        basic_lower = midpoint - multiplier * current_atr
        final_upper = (
            basic_upper
            if final_upper is None or basic_upper < final_upper or previous_close > final_upper
            else final_upper
        )
        final_lower = (
            basic_lower
            if final_lower is None or basic_lower > final_lower or previous_close < final_lower
            else final_lower
        )

        if supertrend_value is None or trend == "SELL":
            if close > final_upper:
                trend = "BUY"
                supertrend_value = final_lower
            else:
                trend = "SELL"
                supertrend_value = final_upper
        elif close < final_lower:
            trend = "SELL"
            supertrend_value = final_upper
        else:
            trend = "BUY"
            supertrend_value = final_lower
        previous_close = close

    return {"value": supertrend_value, "direction": trend}

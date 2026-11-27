from __future__ import annotations

import math
from typing import Any


def _finite(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _validate_candles(candles: list[dict], min_bars: int = 20) -> list[dict] | None:
    closed = [bar for bar in candles if bar.get("is_closed") is True]
    if len(closed) < min_bars:
        return None
    try:
        for bar in closed:
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            v = float(bar.get("volume", 0))
            if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) or v < 0:
                return None
            if l > min(o, c) or h < max(o, c) or l > h:
                return None
    except (KeyError, TypeError, ValueError):
        return None
    return closed


def detect_hunter_sweep_reclaim(
    candles: list[dict[str, Any]],
    *,
    lookback: int = 20,
    rvol_threshold: float = 2.0,
    reclaim_threshold_pct: float = 0.003,
) -> dict[str, Any] | None:
    """
    Detect retail stop-hunt liquidity sweep + reclaim (Hunter entry).
    
    Pattern:
    1. Price sweeps just below Support (buy-side liquidity) or above Resistance (sell-side liquidity)
    2. Candle closes BACK inside the level (reclaim) 
    3. RVOL >= threshold on reclaim candle (institutional absorption)
    4. Signal: HUNTER_SWEEP_RECLAIM - enter WITH the Hunter
    """
    closed = _validate_candles(candles, min_bars=lookback + 1)
    if not closed:
        return None

    window = closed[-lookback - 1:-1]
    current = closed[-1]

    prior_highs = [float(b["high"]) for b in window]
    prior_lows = [float(b["low"]) for b in window]
    prior_closes = [float(b["close"]) for b in window]
    prior_volumes = [float(b.get("volume", 0)) for b in window]

    avg_volume = sum(prior_volumes[-14:]) / max(1, len(prior_volumes[-14:]))
    if avg_volume <= 0:
        return None

    rvol = float(current.get("volume", 0)) / avg_volume if avg_volume > 0 else 0

    equal_highs = []
    for i, h1 in enumerate(prior_highs):
        for h2 in prior_highs[i+1:]:
            if abs(h1 - h2) / h1 <= 0.001:
                equal_highs.append((h1 + h2) / 2)

    equal_lows = []
    for i, l1 in enumerate(prior_lows):
        for l2 in prior_lows[i+1:]:
            if abs(l1 - l2) / l1 <= 0.001:
                equal_lows.append((l1 + l2) / 2)

    range_high = max(prior_highs)
    range_low = min(prior_lows)

    o, h, l, c = float(current["open"]), float(current["high"]), float(current["low"]), float(current["close"])

    results = []

    for level in equal_lows:
        sweep_excursion = level - l
        reclaim = c > level and c > o
        if sweep_excursion > level * reclaim_threshold_pct and reclaim and rvol >= rvol_threshold:
            results.append({
                "type": "BUY_SIDE_SWEEP_RECLAIM",
                "level": level,
                "excursion": sweep_excursion,
                "rvol": rvol,
                "direction": "BUY",
                "explanation": f"Retail stops swept below {level:.2f} (-{sweep_excursion/level*100:.3f}%), reclaimed with RVOL {rvol:.1f}x -> Hunter accumulation",
            })

    for level in equal_highs:
        sweep_excursion = h - level
        reclaim = c < level and c < o
        if sweep_excursion > level * reclaim_threshold_pct and reclaim and rvol >= rvol_threshold:
            results.append({
                "type": "SELL_SIDE_SWEEP_RECLAIM",
                "level": level,
                "excursion": sweep_excursion,
                "rvol": rvol,
                "direction": "SELL",
                "explanation": f"Retail stops swept above {level:.2f} (+{sweep_excursion/level*100:.3f}%), reclaimed with RVOL {rvol:.1f}x -> Hunter distribution",
            })

    if l < range_low and c > range_low and c > o and rvol >= rvol_threshold:
        excursion = range_low - l
        results.append({
            "type": "RANGE_LOW_SWEEP_RECLAIM",
            "level": range_low,
            "excursion": excursion,
            "rvol": rvol,
            "direction": "BUY",
            "explanation": f"Range low {range_low:.2f} swept (-{excursion/range_low*100:.3f}%), reclaimed with RVOL {rvol:.1f}x -> Hunter support defense",
        })

    if h > range_high and c < range_high and c < o and rvol >= rvol_threshold:
        excursion = h - range_high
        results.append({
            "type": "RANGE_HIGH_SWEEP_RECLAIM",
            "level": range_high,
            "excursion": excursion,
            "rvol": rvol,
            "direction": "SELL",
            "explanation": f"Range high {range_high:.2f} swept (+{excursion/range_high*100:.3f}%), reclaimed with RVOL {rvol:.1f}x -> Hunter resistance defense",
        })

    if not results:
        return None

    best = max(results, key=lambda x: x["rvol"])
    return {
        "signal": "HUNTER_SWEEP_RECLAIM",
        "direction": best["direction"],
        "level": best["level"],
        "type": best["type"],
        "rvol": best["rvol"],
        "excursion_pct": best["excursion"] / best["level"] * 100,
        "evidence": best["explanation"],
        "action": "ENTER_WITH_HUNTER",
    }


def detect_whale_absorption_l2(
    candles: list[dict[str, Any]],
    l2_snapshot: dict[str, Any] | None = None,
    *,
    rvol_threshold: float = 2.5,
    max_body_pct: float = 0.005,
    min_absorption_volume: float = 0.0,
) -> dict[str, Any] | None:
    """
    Detect Whale Absorption via tight candle + RVOL spike + L2 wall behavior.
    
    Pattern (Crypto/Binance L2):
    1. RVOL >= 2.5x
    2. Candle body <= 0.5% of price (tight range = absorption)
    3. L2: Large bid/ask walls being hit but price not moving (absorption)
    4. Wall pulls (large orders disappearing) before move = trap
    """
    closed = _validate_candles(candles, min_bars=15)
    if not closed:
        return None

    current = closed[-1]
    prior = closed[-15:-1]

    prior_volumes = [float(b.get("volume", 0)) for b in prior]
    avg_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0
    if avg_volume <= 0:
        return None

    rvol = float(current.get("volume", 0)) / avg_volume
    if rvol < rvol_threshold:
        return None

    o, h, l, c = float(current["open"]), float(current["high"]), float(current["low"]), float(current["close"])
    body_pct = abs(c - o) / c
    range_pct = (h - l) / c

    if body_pct > max_body_pct and range_pct > max_body_pct * 2:
        return None

    absorption_side = "BUY" if c > o else "SELL" if c < o else "NEUTRAL"
    if absorption_side == "NEUTRAL":
        absorption_side = "BUY" if (c - l) > (h - c) else "SELL"

    l2_evidence = ""
    wall_alert = None

    if l2_snapshot and l2_snapshot.get("has_l2"):
        bid_walls = l2_snapshot.get("bid_walls", [])
        ask_walls = l2_snapshot.get("ask_walls", [])
        buyers_pct = _finite(l2_snapshot.get("buyers"), 50) or 50
        sellers_pct = _finite(l2_snapshot.get("sellers"), 50) or 50

        if absorption_side == "BUY" and sellers_pct >= 65:
            wall_alert = "ASK_WALL_ABSORPTION"
            l2_evidence = f"Ask walls absorbing ({sellers_pct:.0f}% sellers) -> Whale bid absorption"
        elif absorption_side == "SELL" and buyers_pct >= 65:
            wall_alert = "BID_WALL_ABSORPTION"
            l2_evidence = f"Bid walls absorbing ({buyers_pct:.0f}% buyers) -> Whale ask absorption"

        large_bid_walls = [w for w in bid_walls if w.get("size_usd", 0) > 500000]
        large_ask_walls = [w for w in ask_walls if w.get("size_usd", 0) > 500000]
        if large_bid_walls or large_ask_walls:
            l2_evidence += f" | L2 Whale Walls: {len(large_bid_walls)} bid / {len(large_ask_walls)} ask >$500k"

    if not l2_evidence:
        l2_evidence = f"Tight candle (body {body_pct*100:.2f}%) + RVOL {rvol:.1f}x -> Institutional absorption"

    return {
        "signal": "WHALE_ABSORPTION",
        "direction": absorption_side,
        "rvol": rvol,
        "body_pct": body_pct * 100,
        "range_pct": range_pct * 100,
        "wall_alert": wall_alert,
        "evidence": l2_evidence,
        "action": "ENTER_WITH_WHALE" if wall_alert in ("ASK_WALL_ABSORPTION", "BID_WALL_ABSORPTION") else "WATCH",
    }


def detect_pump_dump_shield(
    candles: list[dict[str, Any]],
    l2_snapshot: dict[str, Any] | None = None,
    *,
    parabolic_threshold: float = 0.15,
    distribution_rvol: float = 3.0,
    ask_wall_threshold_usd: float = 1_000_000,
) -> dict[str, Any] | None:
    """
    Detect Pump & Dump exhaustion + Whale distribution (Exit-Liquidity Shield).
    
    Pattern (Crypto):
    1. Parabolic move: >15% in last 5-10 candles with accelerating volume
    2. Sudden Ask-side whale wall stacking (distribution)
    3. RVOL spike on RED candle (distribution volume)
    4. Signal: WHALE_DUMP_WARNING - VETO buying, EXIT longs immediately
    """
    closed = _validate_candles(candles, min_bars=10)
    if not closed:
        return None

    recent = closed[-10:]
    current = closed[-1]

    o, h, l, c = float(current["open"]), float(current["high"]), float(current["low"]), float(current["close"])
    
    start_price = float(recent[0]["close"])
    move_pct = (c - start_price) / start_price * 100

    if move_pct < parabolic_threshold * 100:
        return None

    volumes = [float(b.get("volume", 0)) for b in recent]
    vol_trend = volumes[-1] / (sum(volumes[:-1]) / max(1, len(volumes) - 1)) if sum(volumes[:-1]) > 0 else 0

    prior_volumes = [float(b.get("volume", 0)) for b in closed[-15:-5]]
    avg_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0
    rvol = float(current.get("volume", 0)) / avg_volume if avg_volume > 0 else 0

    is_distribution_candle = c < o and rvol >= distribution_rvol

    ask_wall_evidence = ""
    if l2_snapshot and l2_snapshot.get("has_l2"):
        ask_walls = l2_snapshot.get("ask_walls", [])
        large_ask_walls = [w for w in ask_walls if w.get("size_usd", 0) > ask_wall_threshold_usd]
        sellers_pct = _finite(l2_snapshot.get("sellers"), 50) or 50
        
        if large_ask_walls or sellers_pct >= 70:
            ask_wall_evidence = f"Ask-side Whale Stack: {len(large_ask_walls)} walls >${ask_wall_threshold_usd/1e6:.0f}M, Sellers {sellers_pct:.0f}%"

    if not (is_distribution_candle or ask_wall_evidence):
        return None

    evidence_parts = [f"Parabolic +{move_pct:.1f}% in 10 bars"]
    if is_distribution_candle:
        evidence_parts.append(f"Distribution candle (RVOL {rvol:.1f}x)")
    if ask_wall_evidence:
        evidence_parts.append(ask_wall_evidence)

    return {
        "signal": "WHALE_DUMP_WARNING",
        "direction": "SELL",
        "move_pct": move_pct,
        "rvol": rvol,
        "is_distribution_candle": is_distribution_candle,
        "ask_wall_evidence": ask_wall_evidence,
        "evidence": " | ".join(evidence_parts),
        "action": "VETO_BUY_EXIT_LONG",
    }


def detect_fii_dii_oi_alignment(
    candles: list[dict[str, Any]],
    fii_dii_data: dict[str, Any] | None = None,
    oi_data: dict[str, Any] | None = None,
    *,
    min_fii_net_buy: float = 500,  # Cr INR
    min_dii_net_buy: float = 200,  # Cr INR
) -> dict[str, Any] | None:
    """
    Track FII/DII institutional flow + OI state alignment (India/Shoonya).
    
    OI States:
    - Long Buildup: OI ↑ + Price ↑ (Bullish)
    - Short Covering: OI ↓ + Price ↑ (Bullish)
    - Short Buildup: OI ↑ + Price ↓ (Bearish)
    - Long Unwinding: OI ↓ + Price ↓ (Bearish)
    
    Align trade WITH institutional direction.
    """
    closed = _validate_candles(candles, min_bars=10)
    if not closed:
        return None

    current = closed[-1]
    prior = closed[-2] if len(closed) > 1 else current

    price_change_pct = (float(current["close"]) - float(prior["close"])) / float(prior["close"]) * 100
    price_up = price_change_pct > 0.1
    price_down = price_change_pct < -0.1

    oi_state = "UNKNOWN"
    oi_change_pct = 0
    if oi_data:
        oi_change = _finite(oi_data.get("oi_change"), 0) or 0
        oi_change_pct = _finite(oi_data.get("oi_change_pct"), 0) or 0
        oi = _finite(oi_data.get("oi"), 0) or 0
        
        if oi_change > 0 and price_up:
            oi_state = "LONG_BUILDUP"
        elif oi_change < 0 and price_up:
            oi_state = "SHORT_COVERING"
        elif oi_change > 0 and price_down:
            oi_state = "SHORT_BUILDUP"
        elif oi_change < 0 and price_down:
            oi_state = "LONG_UNWINDING"

    fii_net = 0
    dii_net = 0
    inst_direction = "NEUTRAL"
    inst_evidence = ""

    if fii_dii_data:
        fii_net = _finite(fii_dii_data.get("fii_net_buy_cr"), 0) or 0
        dii_net = _finite(fii_dii_data.get("dii_net_buy_cr"), 0) or 0
        
        total_inst = fii_net + dii_net
        if total_inst > min_fii_net_buy + min_dii_net_buy:
            inst_direction = "BUY"
            inst_evidence = f"FII +₹{fii_net:.0f}Cr, DII +₹{dii_net:.0f}Cr -> Institutional BUYING"
        elif total_inst < -(min_fii_net_buy + min_dii_net_buy):
            inst_direction = "SELL"
            inst_evidence = f"FII -₹{abs(fii_net):.0f}Cr, DII -₹{abs(dii_net):.0f}Cr -> Institutional SELLING"
        else:
            inst_evidence = f"FII ₹{fii_net:+.0f}Cr, DII ₹{dii_net:+.0f}Cr -> Mixed/Neutral"

    bullish_oi = oi_state in ("LONG_BUILDUP", "SHORT_COVERING")
    bearish_oi = oi_state in ("SHORT_BUILDUP", "LONG_UNWINDING")

    aligned = False
    conflict = False
    final_direction = "NEUTRAL"

    if inst_direction == "BUY" and bullish_oi:
        aligned = True
        final_direction = "BUY"
    elif inst_direction == "SELL" and bearish_oi:
        aligned = True
        final_direction = "SELL"
    elif inst_direction == "BUY" and bearish_oi:
        conflict = True
    elif inst_direction == "SELL" and bullish_oi:
        conflict = True
    elif inst_direction != "NEUTRAL":
        final_direction = inst_direction
        aligned = True
    elif oi_state != "UNKNOWN":
        final_direction = "BUY" if bullish_oi else "SELL" if bearish_oi else "NEUTRAL"
        aligned = True

    evidence_parts = []
    if inst_evidence:
        evidence_parts.append(inst_evidence)
    if oi_state != "UNKNOWN":
        evidence_parts.append(f"OI State: {oi_state} ({oi_change_pct:+.1f}%)")
    evidence_parts.append(f"Price: {price_change_pct:+.2f}%")

    action = "ALIGN_WITH_INSTITUTIONS" if aligned else "AVOID_CONFLICT" if conflict else "WATCH"

    return {
        "signal": "FII_DII_OI_ALIGNMENT",
        "direction": final_direction,
        "oi_state": oi_state,
        "oi_change_pct": oi_change_pct,
        "fii_net_cr": fii_net,
        "dii_net_cr": dii_net,
        "inst_direction": inst_direction,
        "aligned": aligned,
        "conflict": conflict,
        "evidence": " | ".join(evidence_parts),
        "action": action,
    }


def detect_hunter_unwinding_distribution(
    candles: list[dict[str, Any]],
    l2_snapshot: dict[str, Any] | None = None,
    fii_dii_data: dict[str, Any] | None = None,
    *,
    rvol_threshold: float = 2.0,
) -> dict[str, Any] | None:
    """
    Detect Hunter/Institution UNWINDING (exiting positions) - triggers 100-Agent Swarm IMMEDIATE EXIT.
    
    Patterns:
    1. Crypto: Whale bid walls PULLED + Ask walls stacking + RVOL on red candles
    2. India: FII/DII net selling + OI Long Unwinding + Price breakdown
    3. Both: Sweep of opposite-side liquidity FAILS to reclaim (failed sweep = reversal)
    """
    closed = _validate_candles(candles, min_bars=10)
    if not closed:
        return None

    current = closed[-1]
    prior = closed[-2] if len(closed) > 1 else current

    o, h, l, c = float(current["open"]), float(current["high"]), float(current["low"]), float(current["close"])
    price_down = c < o and c < float(prior["close"])

    rvol = 0
    prior_volumes = [float(b.get("volume", 0)) for b in closed[-15:-1]]
    avg_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0
    if avg_volume > 0:
        rvol = float(current.get("volume", 0)) / avg_volume

    unwind_signals = []

    if fii_dii_data:
        fii_net = _finite(fii_dii_data.get("fii_net_buy_cr"), 0) or 0
        dii_net = _finite(fii_dii_data.get("dii_net_buy_cr"), 0) or 0
        if fii_net < -500 and dii_net < -100 and price_down and rvol >= rvol_threshold:
            unwind_signals.append(f"FII/DII Heavy Selling: FII -₹{abs(fii_net):.0f}Cr, DII -₹{abs(dii_net):.0f}Cr + RVOL {rvol:.1f}x")

    if l2_snapshot and l2_snapshot.get("has_l2"):
        bid_walls = l2_snapshot.get("bid_walls", [])
        ask_walls = l2_snapshot.get("ask_walls", [])
        buyers_pct = _finite(l2_snapshot.get("buyers"), 50) or 50
        sellers_pct = _finite(l2_snapshot.get("sellers"), 50) or 50

        large_bid_pulled = len([w for w in bid_walls if w.get("pulled", False) and w.get("size_usd", 0) > 200000])
        large_ask_stacking = len([w for w in ask_walls if not w.get("pulled", False) and w.get("size_usd", 0) > 500000])

        if large_bid_pulled >= 2 and large_ask_stacking >= 2 and sellers_pct >= 65:
            unwind_signals.append(f"Whale Bid Pull ({large_bid_pulled}) + Ask Stack ({large_ask_stacking}) + Sellers {sellers_pct:.0f}%")

    sweep = None
    prior_highs = [float(b["high"]) for b in closed[-10:-1]]
    prior_lows = [float(b["low"]) for b in closed[-10:-1]]
    if h > max(prior_highs) and c < max(prior_highs):
        sweep = "FAILED_BUY_SIDE_SWEEP"
        unwind_signals.append(f"Failed buy-side sweep above {max(prior_highs):.2f} -> Hunter trap")
    elif l < min(prior_lows) and c > min(prior_lows):
        sweep = "FAILED_SELL_SIDE_SWEEP"
        unwind_signals.append(f"Failed sell-side sweep below {min(prior_lows):.2f} -> Hunter trap")

    if not unwind_signals:
        return None

    return {
        "signal": "HUNTER_UNWINDING",
        "direction": "SELL",
        "sweep_type": sweep,
        "rvol": rvol,
        "evidence": " | ".join(unwind_signals),
        "action": "IMMEDIATE_EXIT_SWARM",
        "swarm_trigger": True,
    }
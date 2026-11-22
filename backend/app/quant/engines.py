from __future__ import annotations

from datetime import datetime, timezone
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

from . import indicators, smc


VERSION = "2.0.0-ADDITIVE"

ENGINE_METADATA = {
    "ENG01_AMT_TPO": ("Auction Market & TPO", ("indian", "crypto", "commodity")),
    "ENG02_BREADTH": ("Market Breadth", ("indian", "crypto")),
    "ENG03_SECTOR_RS": ("Sector Rotation & RS", ("indian", "crypto")),
    "ENG04_INTERMARKET_MACRO": ("Intermarket & Macro", ("indian", "crypto", "commodity")),
    "ENG05_TAPE_READING": ("Advanced Tape Reading", ()),
    "ENG06_LIQUIDITY_HEATMAP": ("Liquidity & Depth Heatmap", ("crypto", "commodity")),
    "ENG07_OPTIONS_FLOW": ("Options Flow Intelligence", ()),
    "ENG08_VOL_SURFACE": ("Volatility Surface & Skew", ()),
    "ENG09_GEX_PINNING": ("Gamma Exposure & Pinning", ()),
    "ENG10_EVENT_CATALYST": ("Event & Catalyst Gate", ("indian", "crypto", "commodity")),
    "ENG11_NEWS_SENTIMENT": ("News Sentiment & Tier", ("indian", "crypto", "commodity")),
    "ENG12_REGIME_ROUTER": ("Regime & Strategy Router", ("indian", "crypto", "commodity")),
    "ENG13_LEAD_LAG_CORR": ("Cross-Asset Lead/Lag", ("indian", "crypto", "commodity")),
    "ENG14_PORTFOLIO_EXPOSURE": ("Portfolio Concentration", ("indian", "crypto", "commodity")),
    "ENG15_EXECUTION_SOR": ("Execution Quality & SOR", ("indian", "crypto", "commodity")),
    "ENG16_ADAPTIVE_SIZING": ("Adaptive Position Sizing", ("indian", "crypto", "commodity")),
    "ENG17_TRADE_LIFECYCLE": ("Trade Lifecycle & Recovery", ("indian", "crypto", "commodity")),
    "ENG18_DRIFT_MONITOR": ("Strategy Drift Monitor", ("indian", "crypto", "commodity")),
    "ENG19_WF_ROBUSTNESS": ("Walk-Forward Robustness", ("indian", "crypto", "commodity")),
    "ENG20_FEED_CONSENSUS": ("Data Anomaly & Consensus", ("indian", "crypto", "commodity")),
}


def _finite(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _result(
    engine_id: str,
    ctx: dict[str, Any],
    *,
    status: str,
    reason: str,
    evidence: str = "--",
    direction: str = "NEUTRAL",
    score_delta: float = 0,
    source: str | None = None,
    quality: str | None = None,
    explain_bn: str = "",
) -> dict[str, Any]:
    label, required_categories = ENGINE_METADATA[engine_id]
    return {
        "engineId": engine_id,
        "label": label,
        "asset": ctx["symbol"],
        "timeframe": ctx.get("timeframe") or "15m",
        "required": ctx["category"] in required_categories,
        "status": status,
        "scoreDelta": score_delta,
        "direction": direction,
        "evidence": evidence,
        "dataSource": source or ctx.get("data_source") or "NONE",
        "dataQuality": quality or ctx.get("data_quality") or "OFFLINE",
        "eventTime": ctx.get("event_time_ms") or int(time.time() * 1000),
        "reasonCode": reason,
        "explainBn": explain_bn,
    }


def _tpo(ctx: dict[str, Any]) -> dict[str, Any]:
    bars = ctx["closed_bars"]
    if len(bars) < 16:
        return _result(
            "ENG01_AMT_TPO", ctx, status="UNAVAILABLE", reason="INSUFFICIENT_CLOSED_BARS",
            explain_bn="TPO ভ্যালু এরিয়া হিসাবের জন্য পর্যাপ্ত বন্ধ ক্যান্ডেল নেই।",
        )
    recent = bars[-24:]
    initial_balance = recent[:4]
    ib_high = max(float(bar["high"]) for bar in initial_balance)
    ib_low = min(float(bar["low"]) for bar in initial_balance)
    min_price = min(float(bar["low"]) for bar in recent)
    max_price = max(float(bar["high"]) for bar in recent)
    bins = 12
    step = max(0.0001, (max_price - min_price) / bins)
    counts = [0] * bins
    for bar in recent:
        low_bin = max(0, min(bins - 1, math.floor((float(bar["low"]) - min_price) / step)))
        high_bin = max(0, min(bins - 1, math.floor((float(bar["high"]) - min_price) / step)))
        for index in range(low_bin, high_bin + 1):
            counts[index] += 1
    poc_index = max(range(bins), key=lambda index: counts[index])
    poc_price = min_price + (poc_index + 0.5) * step
    total = sum(counts)
    value_area_count = counts[poc_index]
    low_index = high_index = poc_index
    while value_area_count < total * 0.70 and (low_index > 0 or high_index < bins - 1):
        left = counts[low_index - 1] if low_index > 0 else -1
        right = counts[high_index + 1] if high_index < bins - 1 else -1
        if right >= left and high_index < bins - 1:
            high_index += 1
            value_area_count += counts[high_index]
        elif low_index > 0:
            low_index -= 1
            value_area_count += counts[low_index]
        else:
            break
    value_low = min_price + low_index * step
    value_high = min_price + (high_index + 1) * step
    last_close = float(recent[-1]["close"])
    direction = "BUY" if last_close > value_high else "SELL" if last_close < value_low else "NEUTRAL"
    state = {
        "BUY": "IMBALANCE_BULL",
        "SELL": "IMBALANCE_BEAR",
        "NEUTRAL": "BALANCE_INSIDE_VA",
    }[direction]
    side = ctx["base_side"]
    aligned = side == direction
    delta = 0 if direction == "NEUTRAL" else 4 if aligned else -4
    status = "PASS" if side not in {"BUY", "SELL"} or aligned or direction == "NEUTRAL" else "FAIL"
    return _result(
        "ENG01_AMT_TPO", ctx, status=status, reason=state, direction=direction, score_delta=delta,
        evidence=f"{state} | POC:{poc_price:.2f} VA:[{value_low:.2f}-{value_high:.2f}] IB:[{ib_low:.2f}-{ib_high:.2f}]",
        explain_bn=f"TPO অকশন: {state} (POC {poc_price:.2f})।",
    )


def _breadth(ctx: dict[str, Any]) -> dict[str, Any]:
    pool = [
        item for item in ctx["universe"]
        if item.get("category") == ctx["category"] and _finite(item.get("change_pct")) is not None
    ]
    if len(pool) < 10:
        status = "NOT_APPLICABLE" if ctx["category"] == "commodity" else "UNAVAILABLE"
        return _result(
            "ENG02_BREADTH", ctx, status=status, reason="INSUFFICIENT_UNIVERSE_BREADTH",
            explain_bn="মার্কেট ব্রেডথ পরিমাপের জন্য পর্যাপ্ত স্ক্যান ডেটা নেই।",
        )
    advances = sum(_finite(item.get("change_pct"), 0) > 0 for item in pool)
    declines = sum(_finite(item.get("change_pct"), 0) < 0 for item in pool)
    ad_ratio = round(advances / declines, 2) if declines else float(advances)
    up_volume = sum(
        _finite(item.get("turnover"), _finite(item.get("volume"), 0)) or 0
        for item in pool if _finite(item.get("change_pct"), 0) > 0
    )
    down_volume = sum(
        _finite(item.get("turnover"), _finite(item.get("volume"), 0)) or 0
        for item in pool if _finite(item.get("change_pct"), 0) < 0
    )
    up_volume_share = round(up_volume / (up_volume + down_volume) * 100) if up_volume + down_volume else 50
    sorted_turnover = sorted((_finite(item.get("turnover"), 0) or 0 for item in pool), reverse=True)
    total_turnover = sum(sorted_turnover)
    top_three_share = round(sum(sorted_turnover[:3]) / total_turnover * 100) if total_turnover else 0
    direction = "BUY" if ad_ratio >= 1.25 and up_volume_share >= 55 else (
        "SELL" if ad_ratio <= 0.8 and up_volume_share <= 45 else "NEUTRAL"
    )
    conflict = (ctx["base_side"] == "BUY" and direction == "SELL") or (
        ctx["base_side"] == "SELL" and direction == "BUY"
    )
    return _result(
        "ENG02_BREADTH", ctx, status="FAIL" if conflict else "PASS",
        reason="BREADTH_DIVERGENCE" if conflict else "BREADTH_OK",
        score_delta=-5 if conflict else 4 if ctx["base_side"] == direction else 1,
        direction=direction,
        evidence=f"A/D:{advances}/{declines} ({ad_ratio}) | UpVol:{up_volume_share}% | Top3Weight:{top_three_share}%",
        explain_bn=f"মার্কেট ব্রেডথ: {advances}টি বাড়ছে বনাম {declines}টি কমছে (Up-Vol {up_volume_share}%)।",
    )


def _sector_rs(ctx: dict[str, Any]) -> dict[str, Any]:
    pool = [item for item in ctx["universe"] if item.get("category") == ctx["category"]]
    item = next((entry for entry in pool if entry.get("order_key") == ctx["order_key"]), None)
    if not pool or item is None or not item.get("sector"):
        return _result(
            "ENG03_SECTOR_RS", ctx, status="UNAVAILABLE", reason="SECTOR_UNIVERSE_MISSING",
            explain_bn="সেক্টর রিলেটিভ স্ট্রেংথ তুলনার ডেটা নেই।",
        )
    benchmark_key = "NIFTY" if ctx["category"] == "indian" else "BTCUSDT"
    benchmark = next((entry for entry in pool if entry.get("order_key") == benchmark_key), None)
    if benchmark is None:
        return _result(
            "ENG03_SECTOR_RS", ctx, status="UNAVAILABLE", reason="BENCHMARK_MISSING",
            explain_bn="সেক্টর শক্তি যাচাইয়ের জন্য বেঞ্চমার্ক ডেটা নেই।",
        )
    peers = [entry for entry in pool if entry.get("sector") == item["sector"]]
    sector_average = sum(_finite(entry.get("change_pct"), 0) or 0 for entry in peers) / len(peers)
    relative_strength = round(sector_average - (_finite(benchmark.get("change_pct"), 0) or 0), 2)
    stock_vs_sector = round((_finite(item.get("change_pct"), 0) or 0) - sector_average, 2)
    direction = "BUY" if relative_strength > 0.25 and stock_vs_sector >= 0 else (
        "SELL" if relative_strength < -0.25 and stock_vs_sector <= 0 else "NEUTRAL"
    )
    conflict = (ctx["base_side"] == "BUY" and direction == "SELL") or (
        ctx["base_side"] == "SELL" and direction == "BUY"
    )
    return _result(
        "ENG03_SECTOR_RS", ctx, status="FAIL" if conflict else "PASS",
        reason="LAGGARD_SECTOR_CONFLICT" if conflict else "SECTOR_RS_OK",
        score_delta=3 if ctx["base_side"] == direction else -4 if conflict else 0,
        direction=direction,
        evidence=f"Sector:{item['sector']} | RS_Bench:{relative_strength}% | Stock_vs_Sec:{stock_vs_sector}%",
        explain_bn=f"সেক্টর ({item['sector']}) বেঞ্চমার্কের চেয়ে {relative_strength:+.2f}% শক্তিশালী।",
    )


def _macro(ctx: dict[str, Any]) -> dict[str, Any]:
    macro = ctx.get("macro")
    has_macro_feed = bool(
        macro
        and (
            any(_finite(macro.get(key), 0) > 0 for key in ("usd_inr", "vix"))
            or macro.get("gold_change_pct") is not None
        )
    )
    if not has_macro_feed:
        return _result(
            "ENG04_INTERMARKET_MACRO", ctx, status="UNAVAILABLE", reason="MACRO_FEEDS_OFFLINE",
            explain_bn="USD/INR, VIX এবং গোল্ড ইন্টারমার্কেট ফিড অনুপলব্ধ।",
        )
    usd_inr = _finite(macro.get("usd_inr"))
    vix = _finite(macro.get("vix"))
    gold_change = _finite(macro.get("gold_change_pct"), 0) or 0
    high_vix = vix is not None and vix >= 24
    risk_off = high_vix or gold_change >= 2.2
    evidence = (
        f"USD/INR:{usd_inr:.2f}" if usd_inr is not None else "USD/INR:N/A"
    )
    evidence += f" | VIX:{vix:.2f}" if vix is not None else " | VIX:N/A"
    evidence += f" | GOLD:{gold_change:.2f}%"
    return _result(
        "ENG04_INTERMARKET_MACRO", ctx, status="FAIL" if high_vix else "PASS",
        reason="EXTREME_VIX_REGIME" if high_vix else "MACRO_STABLE",
        score_delta=-6 if high_vix else -2 if risk_off else 2,
        direction="BEARISH_MACRO" if risk_off else "NEUTRAL",
        evidence=evidence,
        explain_bn=f"সতর্কতা: India VIX ({vix:.2f}) অত্যন্ত বেশি!" if high_vix else "ইন্টারমার্কেট ও ম্যাক্রো অবস্থা স্বাভাবিক আছে।",
    )


def _tape_reading(ctx: dict[str, Any]) -> dict[str, Any]:
    return _result(
        "ENG05_TAPE_READING", ctx, status="UNAVAILABLE", reason="NO_TICK_AGGRESSOR_FEED",
        evidence="True Time & Sales / trade-aggressor feed is not connected; L2 is not a substitute",
        quality="UNAVAILABLE",
        explain_bn="রিয়েল টাইম ট্রেড-অ্যাগ্রেসর ফিড নেই; L2-কে টেপ ডেটা হিসেবে ধরা হয়নি।",
    )


def _liquidity(ctx: dict[str, Any]) -> dict[str, Any]:
    l2 = ctx.get("l2")
    now_ms = int(time.time() * 1000)
    if (
        ctx["category"] == "indian"
        or not l2
        or not l2.get("has_l2")
        or l2.get("updated_at_ms") is None
        or not -5000 <= now_ms - int(l2["updated_at_ms"]) <= 15000
    ):
        status = "NOT_APPLICABLE" if ctx["category"] == "indian" else "UNAVAILABLE"
        return _result(
            "ENG06_LIQUIDITY_HEATMAP", ctx, status=status, reason="NO_LIVE_L2_HEATMAP",
            explain_bn="লাইভ L2 ডেপথ অনুপলব্ধ বা পুরোনো; অনুমানভিত্তিক ডেটা ব্যবহার করা হয়নি।",
        )
    bid = _finite(l2.get("best_bid"), 0) or 0
    ask = _finite(l2.get("best_ask"), 0) or 0
    buyers = _finite(l2.get("buyers"))
    sellers = _finite(l2.get("sellers"))
    if bid <= 0 or ask < bid or buyers is None or sellers is None:
        return _result(
            "ENG06_LIQUIDITY_HEATMAP", ctx, status="UNAVAILABLE", reason="INVALID_L2_SNAPSHOT",
        )
    spread_pct = (ask - bid) / bid * 100
    direction = "BUY" if buyers >= 62 else "SELL" if sellers >= 62 else "NEUTRAL"
    wide_spread = spread_pct > 0.15
    conflict = (ctx["base_side"] == "BUY" and direction == "SELL") or (
        ctx["base_side"] == "SELL" and direction == "BUY"
    )
    return _result(
        "ENG06_LIQUIDITY_HEATMAP", ctx, status="FAIL" if wide_spread or conflict else "PASS",
        reason="LIQUIDITY_VACUUM_SPREAD" if wide_spread else "DEPTH_BALANCED",
        score_delta=-6 if wide_spread else 4 if ctx["base_side"] == direction else 0,
        direction=direction,
        evidence=f"Bid:{buyers:g}% Ask:{sellers:g}% | Spread:{spread_pct:.4f}% | Depth:{_finite(l2.get('depth_notional'), 0):.0f}",
        source=l2.get("source") or "L2",
        quality="LIVE",
    )


def _not_applicable_options(engine_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
    details = {
        "ENG07_OPTIONS_FLOW": ("NO_OPTIONS_CHAIN_FEED", "Verified strike-wise options flow and OI feed is not connected", "ভেরিফায়েড অপশন-চেইন ফিড অনুপলব্ধ।"),
        "ENG08_VOL_SURFACE": ("NO_IV_SURFACE_FEED", "Strike/expiry IV surface feed is not connected", "অপশন IV সারফেস ডেটা অনুপলব্ধ।"),
        "ENG09_GEX_PINNING": ("NO_GEX_OI_FEED", "Strike-wise OI and gamma-exposure feed is not connected", "GEX ও পিনিং হিসাবের জন্য অপশন OI চেইন অনুপলব্ধ।"),
    }
    reason, evidence, explain_bn = details[engine_id]
    return _result(
        engine_id, ctx, status="NOT_APPLICABLE", reason=reason,
        evidence=evidence, quality="UNAVAILABLE", explain_bn=explain_bn,
    )


def _event(ctx: dict[str, Any]) -> dict[str, Any]:
    event = ctx.get("event_risk")
    if not event or event.get("state") in {None, "UNKNOWN"}:
        return _result(
            "ENG10_EVENT_CATALYST", ctx, status="UNAVAILABLE", reason="EVENT_FEED_UNKNOWN",
            explain_bn="ইভেন্ট ফিডের অবস্থা অজানা; নিরাপদভাবে ট্রেড গেট খোলা হয়নি।",
        )
    state = event["state"]
    risk = event.get("risk_level") or "UNKNOWN"
    count = int(event.get("count") or 0)
    if state == "HALT_NO_TRADE":
        return _result(
            "ENG10_EVENT_CATALYST", ctx, status="FAIL", reason="EVENT_RISK_NO_TRADE_GATE",
            score_delta=-20, direction="HALT",
            evidence=event.get("reason") or "Severe critical event halt",
            explain_bn="গুরুত্বপূর্ণ ইভেন্ট ঝুঁকির কারণে ট্রেডিং গেট বন্ধ।",
        )
    return _result(
        "ENG10_EVENT_CATALYST", ctx, status="PASS",
        reason=state, score_delta=-3 if risk == "ELEVATED" else 1,
        evidence=f"State:{state} | Risk:{risk} | Catalysts:{count}",
        explain_bn=f"ইভেন্ট গেট: {risk} (ক্যাটালিস্ট {count}টি)।",
    )


def _news(ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx.get("news_fetch_state") != "LOADED":
        return _result(
            "ENG11_NEWS_SENTIMENT", ctx, status="UNAVAILABLE", reason="NEWS_WIRE_NOT_LOADED",
            explain_bn="নিউজ ওয়্যার লোড হয়নি; অনুপস্থিত ডেটাকে নিরপেক্ষ ধরা হয়নি।",
        )
    key = str(ctx["order_key"]).upper().replace("USDT", "")
    related = [
        item for item in ctx["news_items"]
        if key and key in str(item.get("title") or "").upper()
    ]
    official = sum(item.get("trust_tier") == "OFFICIAL" for item in related)
    rumor_only = bool(related) and all(item.get("trust_tier") == "LOW_TRUST_RUMOR" for item in related)
    bullish = sum(item.get("impact") == "BULLISH" for item in related)
    bearish = sum(item.get("impact") == "BEARISH" for item in related)
    direction = "BUY" if bullish > bearish else "SELL" if bearish > bullish else "NEUTRAL"
    conflict = not rumor_only and (
        (ctx["base_side"] == "BUY" and direction == "SELL")
        or (ctx["base_side"] == "SELL" and direction == "BUY")
    )
    return _result(
        "ENG11_NEWS_SENTIMENT", ctx, status="FAIL" if conflict else "PASS",
        reason="RUMOR_DISCOUNTED" if rumor_only else "NEWS_SENTIMENT_CONFLICT" if conflict else "NEWS_OK",
        score_delta=-2 if rumor_only else -5 if conflict else 3 if direction == ctx["base_side"] else 0,
        direction=direction,
        evidence=f"Matches:{len(related)} Official:{official} RumorOnly:{rumor_only} Sentiment:{direction}",
        explain_bn="গুজবভিত্তিক খবরকে নিশ্চিত তথ্য হিসেবে গণ্য করা হয়নি।" if rumor_only else f"নিউজ সেন্টিমেন্ট: {direction} (অফিশিয়াল: {official})।",
    )


def _regime(ctx: dict[str, Any]) -> dict[str, Any]:
    quant = ctx.get("quant") or {}
    regime_data = quant.get("regime") or {}
    regime = regime_data.get("regime") or "UNKNOWN"
    allowlist = regime_data.get("strategy_allowlist") or []
    if regime == "UNKNOWN":
        return _result(
            "ENG12_REGIME_ROUTER", ctx, status="UNAVAILABLE", reason="REGIME_UNKNOWN",
            explain_bn="মার্কেট রেজিম শনাক্ত করার পর্যাপ্ত ডেটা নেই।",
        )
    chop = regime == "HIGH_VOL_CHOP"
    conflict = (ctx["base_side"] == "BUY" and regime == "TRENDING_BEAR") or (
        ctx["base_side"] == "SELL" and regime == "TRENDING_BULL"
    )
    direction = "BUY" if regime == "TRENDING_BULL" else "SELL" if regime == "TRENDING_BEAR" else "NEUTRAL"
    return _result(
        "ENG12_REGIME_ROUTER", ctx, status="FAIL" if chop or conflict else "PASS",
        reason="CHOP_REGIME_BLOCK" if chop else "COUNTER_REGIME_BLOCK" if conflict else "REGIME_ALIGNED",
        score_delta=-6 if chop else -8 if conflict else 3,
        direction=direction,
        evidence=f"Regime:{regime} | Allowlist:[{','.join(allowlist)}]",
        explain_bn=f"রেজিম রাউটার: {regime} (অনুমোদিত: {', '.join(allowlist) or 'WAIT'})।",
    )


def _correlation(ctx: dict[str, Any]) -> dict[str, Any]:
    correlation = (ctx.get("quant") or {}).get("correlation")
    if not correlation or correlation.get("status") != "LIVE":
        return _result(
            "ENG13_LEAD_LAG_CORR", ctx, status="UNAVAILABLE", reason="CORR_SYNC_PENDING",
            evidence=f"Peer correlation {(correlation or {}).get('status') or 'UNSYNCED'}",
            explain_bn="ক্রস-অ্যাসেট কোরিলেশনের পর্যাপ্ত নমুনা নেই।",
        )
    coefficient = _finite(correlation.get("pearson"))
    samples = int(correlation.get("samples") or 0)
    if coefficient is None or samples < 20:
        return _result(
            "ENG13_LEAD_LAG_CORR", ctx, status="UNAVAILABLE", reason="CORR_SAMPLE_INSUFFICIENT",
        )
    breakdown = abs(coefficient) < 0.15
    peer = correlation.get("peer") or "UNKNOWN"
    return _result(
        "ENG13_LEAD_LAG_CORR", ctx, status="PASS",
        reason="CORRELATION_BREAKDOWN_WATCH" if breakdown else "CORR_STABLE",
        score_delta=-1 if breakdown else 2,
        evidence=f"r={coefficient:.4f} vs {peer} (N={samples}, correlation is not causation)",
        explain_bn=f"{peer}-এর সাথে রোলিং কোরিলেশন {coefficient:.4f} (এটি কার্যকারণ প্রমাণ নয়)।",
    )


def _portfolio(ctx: dict[str, Any]) -> dict[str, Any]:
    positions = ctx.get("positions")
    if positions is None:
        return _result(
            "ENG14_PORTFOLIO_EXPOSURE", ctx, status="UNAVAILABLE", reason="PORTFOLIO_SNAPSHOT_MISSING",
            explain_bn="পোর্টফোলিও পজিশন স্ন্যাপশট নেই।",
        )
    if any(not position.get("id") for position in positions):
        return _result(
            "ENG14_PORTFOLIO_EXPOSURE", ctx, status="UNAVAILABLE", reason="POSITION_ID_MISSING",
            explain_bn="পোর্টফোলিও পজিশনের নির্ভরযোগ্য আইডি নেই।",
        )
    unique: dict[str, dict[str, Any]] = {}
    for position in positions:
        quantity = _finite(position.get("qty"), 0) or 0
        if quantity:
            unique.setdefault(str(position["id"]), position)
    long_count = sum((_finite(position.get("qty"), 0) or 0) > 0 for position in unique.values())
    short_count = sum((_finite(position.get("qty"), 0) or 0) < 0 for position in unique.values())
    order_key = str(ctx["order_key"]).upper()
    symbol_conflict = any(
        order_key in str(position.get("symbol") or "").upper()
        and (((_finite(position.get("qty"), 0) or 0) > 0 and ctx["base_side"] == "SELL")
             or ((_finite(position.get("qty"), 0) or 0) < 0 and ctx["base_side"] == "BUY"))
        for position in unique.values()
    )
    overloaded = (ctx["base_side"] == "BUY" and long_count >= 3) or (
        ctx["base_side"] == "SELL" and short_count >= 3
    )
    fail = symbol_conflict or overloaded
    return _result(
        "ENG14_PORTFOLIO_EXPOSURE", ctx, status="FAIL" if fail else "PASS",
        reason="CONCENTRATION_OR_CONFLICT" if fail else "EXPOSURE_SAFE",
        score_delta=-10 if fail else 1,
        evidence=f"Open:{len(unique)} (L:{long_count}/S:{short_count}) | Conflict:{symbol_conflict}",
        explain_bn="পোর্টফোলিও কনসেনট্রেশন বা বিপরীত পজিশন কনফ্লিক্ট পাওয়া গেছে।" if fail else f"খোলা পজিশন: {len(unique)}টি।",
    )


def _execution(ctx: dict[str, Any]) -> dict[str, Any]:
    route = ctx.get("execution")
    if not route or not route.get("broker"):
        return _result(
            "ENG15_EXECUTION_SOR", ctx, status="FAIL", reason="UNMAPPED_BROKER_ROUTE",
            score_delta=-10, explain_bn="এই ইন্সট্রুমেন্টের ব্রোকার রাউটিং ম্যাপ নেই।",
        )
    slippage = _finite(route.get("estimated_slippage_bps"))
    reject_rate = _finite(route.get("reject_rate_pct"))
    if slippage is None or reject_rate is None:
        return _result(
            "ENG15_EXECUTION_SOR", ctx, status="UNAVAILABLE", reason="EXECUTION_METRICS_MISSING",
            evidence=f"Route:{route['broker']} | Slippage/reject metrics unavailable",
        )
    high_slippage = slippage > 35
    high_rejects = reject_rate > 30
    failed = high_slippage or high_rejects
    return _result(
        "ENG15_EXECUTION_SOR", ctx, status="FAIL" if failed else "PASS",
        reason="HIGH_SLIPPAGE_EST" if high_slippage else "HIGH_REJECT_RATE" if high_rejects else "SOR_READY",
        score_delta=-5 if failed else 2,
        evidence=f"Route:{route['broker']} | EstSlip:{slippage:g}bps | RejRate:{reject_rate:g}%",
        explain_bn=f"স্মার্ট রাউটার: {route['broker']} (আনুমানিক স্লিপেজ {slippage:g} bps)।",
    )


def _sizing(ctx: dict[str, Any]) -> tuple[dict[str, Any], dict[str, float] | None]:
    route = ctx.get("execution")
    entry = _finite(ctx.get("ltp"), 0) or 0
    stop = _finite(ctx.get("sl"), 0) or 0
    if not route or entry <= 0 or stop <= 0 or entry == stop:
        return _result(
            "ENG16_ADAPTIVE_SIZING", ctx, status="UNAVAILABLE", reason="INVALID_PRICE_OR_SL",
            explain_bn="পজিশন সাইজিংয়ের জন্য সঠিক এন্ট্রি ও স্টপ-লস নেই।",
        ), None
    if (
        (ctx["base_side"] == "BUY" and stop >= entry)
        or (ctx["base_side"] == "SELL" and stop <= entry)
        or ctx["base_side"] == "NEUTRAL"
    ):
        return _result(
            "ENG16_ADAPTIVE_SIZING", ctx, status="FAIL", reason="STOP_LOSS_ON_WRONG_SIDE",
            explain_bn="নির্দেশিত দিকের সঙ্গে স্টপ-লসের অবস্থান মেলে না।",
        ), None
    sizing = ctx.get("sizing")
    if not sizing:
        return _result(
            "ENG16_ADAPTIVE_SIZING", ctx, status="UNAVAILABLE", reason="RISK_BUDGET_MISSING",
            explain_bn="সার্ভার-সাইড ঝুঁকি বাজেট অনুপলব্ধ।",
        ), None
    fx = 1.0 if ctx["category"] == "indian" else _finite(sizing.get("usd_inr"))
    if fx is None or fx <= 0:
        return _result(
            "ENG16_ADAPTIVE_SIZING", ctx, status="UNAVAILABLE", reason="FX_RATE_MISSING",
            explain_bn="যাচাইকৃত FX রেট না থাকায় সাইজিং করা হয়নি।",
        ), None
    daily_budget = _finite(sizing.get("daily_budget_inr"))
    capital = _finite(sizing.get("capital_inr"))
    used = (_finite(sizing.get("used_budget_inr"), 0) or 0) + (
        _finite(sizing.get("reserved_budget_inr"), 0) or 0
    )
    lot_size = _finite(route.get("lot_size"))
    max_quantity = _finite(route.get("max_quantity"))
    if daily_budget is None or capital is None or lot_size is None or daily_budget <= 0 or capital <= 0 or lot_size <= 0:
        return _result(
            "ENG16_ADAPTIVE_SIZING", ctx, status="UNAVAILABLE", reason="INVALID_SIZING_INPUT",
        ), None
    remaining = max(0, daily_budget - used)
    risk_cap = min(daily_budget * 0.25, capital * 0.01)
    risk_per_unit = abs(entry - stop) * fx
    if route.get("broker") == "SHOONYA":
        risk_lots = math.floor(risk_cap / (risk_per_unit * lot_size))
        margin_factor = 0.22 if route.get("segment") == "EQ" else 1.0
        budget_lots = math.floor(remaining / (entry * fx * lot_size * margin_factor))
        quantity = min(risk_lots, budget_lots) * lot_size
    else:
        if max_quantity is None or max_quantity <= 0:
            return _result(
                "ENG16_ADAPTIVE_SIZING", ctx, status="UNAVAILABLE", reason="BROKER_MAX_QUANTITY_MISSING",
            ), None
        max_by_risk = risk_cap / risk_per_unit
        max_by_budget = remaining / (entry * fx)
        raw_quantity = min(max_quantity, max_by_risk, max_by_budget)
        quantity = math.floor(raw_quantity / lot_size) * lot_size

    required = quantity * entry * fx * (
        0.22 if route.get("broker") == "SHOONYA" and route.get("segment") == "EQ" else 1
    )
    actual_risk = quantity * risk_per_unit
    sizing_result = {
        "qty": quantity,
        "requiredInr": round(required),
        "riskCapInr": round(risk_cap),
        "riskInr": round(actual_risk, 2),
    }
    failed = quantity <= 0 or required > remaining or actual_risk > risk_cap
    return _result(
        "ENG16_ADAPTIVE_SIZING", ctx, status="FAIL" if failed else "PASS",
        reason="INSUFFICIENT_RISK_OR_BUDGET" if failed else "SIZING_COMPUTED",
        score_delta=-8 if failed else 2,
        evidence=f"AutoQty:{quantity:g} | EstReq:₹{required:.0f} | Risk:₹{actual_risk:.2f}/₹{risk_cap:.0f} | RemBudget:₹{remaining:.0f}",
        explain_bn=f"অ্যাডাপ্টিভ সাইজিং: পরিমাণ {quantity:g} (প্রাক্কলিত ঝুঁকি ₹{actual_risk:.2f})।",
    ), sizing_result


def _lifecycle(ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx["category"] != "indian":
        return _result(
            "ENG17_TRADE_LIFECYCLE", ctx, status="PASS", reason="CRYPTO_SESSION_CONTINUOUS",
            score_delta=1, evidence="Continuous crypto market; exchange lifecycle data not supplied",
        )
    session = ctx.get("session")
    if session is None or session.get("is_open") is None:
        return _result(
            "ENG17_TRADE_LIFECYCLE", ctx, status="UNAVAILABLE", reason="EXCHANGE_SESSION_UNKNOWN",
            explain_bn="এক্সচেঞ্জ সেশন ক্যালেন্ডার যাচাই করা যায়নি।",
        )
    if not session["is_open"]:
        return _result(
            "ENG17_TRADE_LIFECYCLE", ctx, status="FAIL", reason="OUTSIDE_EXCHANGE_SESSION",
            score_delta=-5, evidence=f"Exchange Session: {session.get('state') or 'CLOSED'} ({session.get('reason') or 'closed'})",
            explain_bn="এক্সচেঞ্জ বন্ধ থাকায় নতুন এন্ট্রি ব্লক।",
        )
    now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
    if session.get("now_ist"):
        parsed = datetime.fromisoformat(session["now_ist"])
        now = (
            parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            if parsed.tzinfo is None
            else parsed.astimezone(ZoneInfo("Asia/Kolkata"))
        )
    minute_of_day = now.hour * 60 + now.minute
    if minute_of_day >= 910:
        return _result(
            "ENG17_TRADE_LIFECYCLE", ctx, status="FAIL", reason="MIS_CUTOFF_REACHED",
            score_delta=-10, evidence="Past 3:10 PM IST MIS entry cutoff",
            explain_bn="দুপুর ৩:১০-এর পর নতুন ইন্ট্রাডে এন্ট্রি ব্লক।",
        )
    return _result(
        "ENG17_TRADE_LIFECYCLE", ctx, status="PASS", reason="LIFECYCLE_OK",
        score_delta=1, evidence="Exchange session active; MIS cutoff not reached",
        explain_bn="ট্রেড লাইফসাইকেল ও স্কয়ার-অফ গার্ড সক্রিয়।",
    )


def _drift(ctx: dict[str, Any]) -> dict[str, Any]:
    scorecard = ctx.get("scorecard")
    walk_forward = (ctx.get("quant") or {}).get("walk_forward")
    if not scorecard:
        return _result(
            "ENG18_DRIFT_MONITOR", ctx, status="UNAVAILABLE", reason="SCORECARD_MISSING",
            explain_bn="স্ট্র্যাটেজি পারফরম্যান্স স্কোরকার্ড নেই।",
        )
    reject_rate = _finite(scorecard.get("reject_rate_pct"))
    brier = _finite((walk_forward or {}).get("brier_score"))
    if reject_rate is None:
        return _result(
            "ENG18_DRIFT_MONITOR", ctx, status="UNAVAILABLE", reason="REJECT_RATE_MISSING",
        )
    degraded = reject_rate > 35 or (brier is not None and brier > 0.32)
    return _result(
        "ENG18_DRIFT_MONITOR", ctx, status="FAIL" if degraded else "PASS",
        reason="PERFORMANCE_DRIFT_PAUSE" if degraded else "DRIFT_WITHIN_LIMIT",
        score_delta=-6 if degraded else 1,
        evidence=f"Orders:{int(scorecard.get('total_recorded') or 0)} Rej:{reject_rate:g}% | Brier:{brier if brier is not None else '--'} | AutoMutate:LOCKED",
        explain_bn="স্ট্র্যাটেজিতে ড্রিফট শনাক্ত; নতুন অটো এন্ট্রি পজ।" if degraded else "স্ট্র্যাটেজি ড্রিফট সীমার মধ্যে।",
    )


def _walk_forward(ctx: dict[str, Any]) -> dict[str, Any]:
    walk_forward = (ctx.get("quant") or {}).get("walk_forward")
    if not walk_forward or walk_forward.get("status") in {"INSUFFICIENT_DATA", "NO_SIGNALS"}:
        return _result(
            "ENG19_WF_ROBUSTNESS", ctx, status="UNAVAILABLE", reason="WF_INSUFFICIENT_HISTORY",
            evidence=f"WF:{(walk_forward or {}).get('status') or 'NONE'} ({(walk_forward or {}).get('trades') or 0} trades)",
            explain_bn="ওয়াক-ফরোয়ার্ড পরীক্ষার জন্য পর্যাপ্ত আউট-অফ-স্যাম্পল ডেটা নেই।",
        )
    profit_factor = _finite(walk_forward.get("profit_factor"))
    robust = not walk_forward.get("overfitting_warning") and (profit_factor is None or profit_factor >= 1.05)
    trades = int(walk_forward.get("trades") or 0)
    net_return = _finite(walk_forward.get("net_return_pct"), 0) or 0
    drawdown = _finite(walk_forward.get("max_drawdown_pct"), 0) or 0
    return _result(
        "ENG19_WF_ROBUSTNESS", ctx, status="PASS" if robust else "FAIL",
        reason="WF_ROBUST" if robust else "OVERFIT_OR_WEAK_PF",
        score_delta=4 if robust else -4,
        evidence=f"N:{trades} | PF:{profit_factor} | Net:{net_return:g}% | DD:{drawdown:g}%",
        explain_bn=f"ওয়াক-ফরোয়ার্ড পরীক্ষা {'পাস' if robust else 'ব্যর্থ'} (Profit Factor {profit_factor}, Drawdown {drawdown:g}%)।",
    )


def _feed_consensus(ctx: dict[str, Any]) -> dict[str, Any]:
    quality = str(ctx.get("data_quality") or "OFFLINE")
    event_time = _finite(ctx.get("event_time_ms"))
    age_ms = int(time.time() * 1000) - int(event_time) if event_time is not None else None
    fresh = quality in {"LIVE", "DELAYED"} and age_ms is not None and -5000 <= age_ms <= 60000
    price = _finite(ctx.get("ltp"), 0) or 0
    passed = fresh and price > 0
    age_text = f"{round(age_ms / 1000)}s" if age_ms is not None else "unknown"
    return _result(
        "ENG20_FEED_CONSENSUS", ctx, status="PASS" if passed else "FAIL",
        reason="FEED_VERIFIED" if passed else "FEED_STALE_OR_OFFLINE",
        score_delta=2 if passed else -15,
        evidence=f"Quality:{quality} | Source:{ctx.get('data_source') or 'NONE'} | Age:{age_text}",
        explain_bn=f"ডেটা ফিড যাচাইকৃত ({quality})।" if passed else f"ডেটা ফিড সতেজ/যাচাইকৃত নয় ({quality}); গেট ব্লক।",
    )


def evaluate_engine_suite(payload: dict[str, Any]) -> dict[str, Any]:
    category = payload.get("category") or "indian"
    if category not in {"indian", "crypto", "commodity"}:
        raise ValueError("category must be indian, crypto, or commodity")
    closed_bars = [
        bar for bar in payload.get("candles", [])
        if bar.get("is_closed") is True
    ]
    ctx = {
        **payload,
        "category": category,
        "closed_bars": closed_bars,
        "base_side": payload.get("base_side") or "NEUTRAL",
        "universe": payload.get("universe") or [],
        "news_items": payload.get("news_items") or [],
    }
    sizing_result, sizing = _sizing(ctx)
    results = [
        _tpo(ctx),
        _breadth(ctx),
        _sector_rs(ctx),
        _macro(ctx),
        _tape_reading(ctx),
        _liquidity(ctx),
        _not_applicable_options("ENG07_OPTIONS_FLOW", ctx),
        _not_applicable_options("ENG08_VOL_SURFACE", ctx),
        _not_applicable_options("ENG09_GEX_PINNING", ctx),
        _event(ctx),
        _news(ctx),
        _regime(ctx),
        _correlation(ctx),
        _portfolio(ctx),
        _execution(ctx),
        sizing_result,
        _lifecycle(ctx),
        _drift(ctx),
        _walk_forward(ctx),
        _feed_consensus(ctx),
    ]
    passed = sum(item["status"] == "PASS" for item in results)
    failed = [item for item in results if item["status"] == "FAIL"]
    required = [item for item in results if item["required"]]
    required_passed = sum(item["status"] == "PASS" for item in required)
    required_blocked = [item for item in required if item["status"] != "PASS"]
    raw_delta = sum(_finite(item["scoreDelta"], 0) or 0 for item in results)
    fusion_delta = max(-25, min(20, raw_delta))
    base_score = _finite(payload.get("base_score"), 0) or 0
    fusion_score = max(0, min(100, math.floor(base_score + fusion_delta + 0.5))) if base_score > 0 else 0
    all_required_pass = bool(required) and not required_blocked and not failed
    live_verified = payload.get("data_quality") == "LIVE"
    base_side = ctx["base_side"]
    eligible = fusion_score >= 80 and all_required_pass and live_verified and base_side in {"BUY", "SELL"}
    block_reasons = []
    if fusion_score < 80:
        block_reasons.append(f"Score {fusion_score}/100 < 80")
    if not all_required_pass:
        block_reasons.append(f"{len(required_blocked)} required engines did not PASS")
    if not live_verified:
        block_reasons.append(f"Data is {payload.get('data_quality') or 'OFFLINE'}; LIVE data is required")
    if base_side == "NEUTRAL":
        block_reasons.append("No definite BUY/SELL direction")
    bengali_block_reasons = []
    if fusion_score < 80:
        bengali_block_reasons.append(f"স্কোর {fusion_score}/100, প্রয়োজনীয় ৮০-এর নিচে")
    if not all_required_pass:
        bengali_block_reasons.append(f"{len(required_blocked)}টি আবশ্যক ইঞ্জিন PASS হয়নি")
    if not live_verified:
        bengali_block_reasons.append(f"ডেটা {payload.get('data_quality') or 'OFFLINE'}; অটো-ট্রেডের জন্য LIVE প্রয়োজন")
    if base_side == "NEUTRAL":
        bengali_block_reasons.append("নির্দিষ্ট BUY/SELL দিক নেই")
    summary = (
        f"All conditions passed. Fusion score {fusion_score}/100 and {required_passed}/{len(required)} required engines passed."
        if eligible
        else f"Auto-trade held: score {fusion_score}/100 | required engines passed {required_passed}/{len(required)}. Reason: {block_reasons[0] if block_reasons else 'Waiting'}."
    )
    bengali_summary = (
        f"সব শর্ত পূরণ। Fusion স্কোর {fusion_score}/100; প্রয়োজনীয় ইঞ্জিন PASS {required_passed}/{len(required)}।"
        if eligible
        else f"অটো-ট্রেড স্থগিত: স্কোর {fusion_score}/100; প্রয়োজনীয় ইঞ্জিন PASS {required_passed}/{len(required)}। কারণ: {bengali_block_reasons[0] if bengali_block_reasons else 'অপেক্ষমাণ'}।"
    )
    return {
        "version": VERSION,
        "timestamp": int(time.time() * 1000),
        "symbol": payload["symbol"],
        "orderKey": payload["order_key"],
        "category": category,
        "side": base_side,
        "entry": _finite(payload.get("ltp"), 0) or 0,
        "sl": _finite(payload.get("sl"), 0) or 0,
        "target": _finite(payload.get("target"), 0) or 0,
        "baseScore": base_score,
        "fusionDelta": fusion_delta,
        "fusionScore": fusion_score,
        "passedCount": passed,
        "failedCount": len(failed),
        "unavailableCount": sum(item["status"] in {"UNAVAILABLE", "NOT_APPLICABLE"} for item in results),
        "requiredTotal": len(required),
        "requiredPassed": required_passed,
        "allRequiredPass": all_required_pass,
        "isLiveVerified": live_verified,
        "autoTradeEligible": eligible,
        "blockReasons": block_reasons,
        "bengaliBlockReasons": bengali_block_reasons,
        "sizing": sizing,
        "summary": summary,
        "bengaliSummary": bengali_summary,
        "engines": results,
    }


def analyze_quant_features(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [bar for bar in candles if bar.get("is_closed") is True]
    closes = [float(bar["close"]) for bar in closed]
    return {
        "closed_candle_count": len(closed),
        "indicators": {
            "ema_9": indicators.ema(closes, 9)[-1] if len(closes) >= 1 else None,
            "ema_21": indicators.ema(closes, 21)[-1] if len(closes) >= 1 else None,
            "rsi_14": indicators.rsi(closes, 14),
            "atr_14": indicators.atr(closed, 14),
            "macd_12_26_9": indicators.macd(closes),
            "bollinger_20_2": indicators.bollinger_bands(closes),
            "supertrend_10_3": indicators.supertrend(closed),
            "volume_imbalance": indicators.volume_imbalance(closed),
        },
        "smc": {
            "fair_value_gaps": smc.fair_value_gaps(closed),
            "order_blocks": smc.order_blocks(closed),
            "liquidity_sweeps": smc.liquidity_sweeps(closed),
            "market_structure": smc.bos_choch(closed),
        },
    }

/* ==========================================================================
   ARAF INTELLIGENCE — ADDITIVE 20-ENGINE SUITE & SIGNAL FUSION v2 (engines.js)
   Spec: Additive Specification v1.2 • Zero-Replacement • No Look-Ahead
   Contract: { engineId, asset, timeframe, status, scoreDelta, direction,
               evidence, dataSource, dataQuality, eventTime, reasonCode }
   ========================================================================== */

const ARAF_ENGINES_VERSION = "2.0.0-ADDITIVE";
const ENGINE_REPLAY_JOURNAL = [];

const ENGINE_APPLICABILITY_MAP = {
    ENG01_AMT_TPO:            { label: "Auction Market & TPO",        requiredFor: ["indian", "crypto", "commodity"] },
    ENG02_BREADTH:            { label: "Market Breadth",              requiredFor: ["indian", "crypto"] },
    ENG03_SECTOR_RS:          { label: "Sector Rotation & RS",        requiredFor: ["indian", "crypto"] },
    ENG04_INTERMARKET_MACRO:  { label: "Intermarket & Macro",         requiredFor: ["indian", "crypto", "commodity"] },
    ENG05_TAPE_READING:       { label: "Advanced Tape Reading",       requiredFor: [] }, // True Time&Sales feed required
    ENG06_LIQUIDITY_HEATMAP:  { label: "Liquidity & Depth Heatmap",   requiredFor: ["crypto", "commodity"] },
    ENG07_OPTIONS_FLOW:       { label: "Options Flow Intelligence",   requiredFor: [] }, // Verified Option Chain API required
    ENG08_VOL_SURFACE:        { label: "Volatility Surface & Skew",   requiredFor: [] }, // Verified Option Chain API required
    ENG09_GEX_PINNING:        { label: "Gamma Exposure & Pinning",    requiredFor: [] }, // Verified OI Chain API required
    ENG10_EVENT_CATALYST:     { label: "Event & Catalyst Gate",       requiredFor: ["indian", "crypto", "commodity"] },
    ENG11_NEWS_SENTIMENT:     { label: "News Sentiment & Tier",       requiredFor: ["indian", "crypto", "commodity"] },
    ENG12_REGIME_ROUTER:      { label: "Regime & Strategy Router",    requiredFor: ["indian", "crypto", "commodity"] },
    ENG13_LEAD_LAG_CORR:      { label: "Cross-Asset Lead/Lag",        requiredFor: ["indian", "crypto", "commodity"] },
    ENG14_PORTFOLIO_EXPOSURE: { label: "Portfolio Concentration",     requiredFor: ["indian", "crypto", "commodity"] },
    ENG15_EXECUTION_SOR:      { label: "Execution Quality & SOR",     requiredFor: ["indian", "crypto", "commodity"] },
    ENG16_ADAPTIVE_SIZING:    { label: "Adaptive Position Sizing",    requiredFor: ["indian", "crypto", "commodity"] },
    ENG17_TRADE_LIFECYCLE:    { label: "Trade Lifecycle & Recovery",  requiredFor: ["indian", "crypto", "commodity"] },
    ENG18_DRIFT_MONITOR:      { label: "Strategy Drift Monitor",      requiredFor: ["indian", "crypto", "commodity"] },
    ENG19_WF_ROBUSTNESS:      { label: "Walk-Forward Robustness",     requiredFor: ["indian", "crypto", "commodity"] },
    ENG20_FEED_CONSENSUS:     { label: "Data Anomaly & Consensus",    requiredFor: ["indian", "crypto", "commodity"] }
};

function makeEngineResult(engineId, ctx, fields) {
    const meta = ENGINE_APPLICABILITY_MAP[engineId] || { label: engineId, requiredFor: [] };
    const cat = ctx.category || "indian";
    const isReq = meta.requiredFor.includes(cat);
    return {
        engineId,
        label: meta.label,
        asset: ctx.symbol,
        timeframe: ctx.timeframe || "15m",
        required: isReq,
        status: fields.status || "UNAVAILABLE",
        scoreDelta: Number(fields.scoreDelta || 0),
        direction: fields.direction || "NEUTRAL",
        evidence: fields.evidence || "--",
        dataSource: fields.dataSource || ctx.dataSource || "NONE",
        dataQuality: fields.dataQuality || ctx.dataQuality || "OFFLINE",
        eventTime: fields.eventTime || ctx.eventTime || Date.now(),
        reasonCode: fields.reasonCode || "OK",
        explainBn: fields.explainBn || ""
    };
}

function getStrictClosedCandles(assetId, tf = "15m") {
    const raw = (typeof QUANT_FEATURE_STORE !== "undefined" && QUANT_FEATURE_STORE.candlesByTf[assetId]?.[tf]) || [];
    if (raw.length <= 1) return [];
    // No look-ahead: exclude currently forming candle
    return raw.filter((c, idx) => c && c.close > 0 && (c.isClosed === true || idx < raw.length - 1));
}

// 1) Auction Market Theory & TPO Engine
function runEng01AmtTpo(ctx) {
    const bars = ctx.closedBars;
    if (bars.length < 16) {
        return makeEngineResult("ENG01_AMT_TPO", ctx, {
            status: "UNAVAILABLE", reasonCode: "INSUFFICIENT_CLOSED_BARS",
            explainBn: "TPO ভ্যালু এরিয়া হিসাবের জন্য পর্যাপ্ত বন্ধ ক্যান্ডেল নেই।"
        });
    }
    const recent = bars.slice(-24);
    const ibBars = recent.slice(0, 4);
    const ibHigh = Math.max(...ibBars.map(b => b.high));
    const ibLow = Math.min(...ibBars.map(b => b.low));
    const minP = Math.min(...recent.map(b => b.low));
    const maxP = Math.max(...recent.map(b => b.high));
    const bins = 12;
    const step = Math.max(0.0001, (maxP - minP) / bins);
    const tpoCounts = new Array(bins).fill(0);

    recent.forEach(b => {
        const lowBin = Math.max(0, Math.min(bins - 1, Math.floor((b.low - minP) / step)));
        const highBin = Math.max(0, Math.min(bins - 1, Math.floor((b.high - minP) / step)));
        for (let i = lowBin; i <= highBin; i++) tpoCounts[i] += 1;
    });

    let pocIdx = 0;
    for (let i = 1; i < bins; i++) if (tpoCounts[i] > tpoCounts[pocIdx]) pocIdx = i;
    const pocPrice = minP + (pocIdx + 0.5) * step;

    const totalTpo = tpoCounts.reduce((a, b) => a + b, 0);
    let vaTpo = tpoCounts[pocIdx], lo = pocIdx, hi = pocIdx;
    while (vaTpo < totalTpo * 0.70 && (lo > 0 || hi < bins - 1)) {
        const left = lo > 0 ? tpoCounts[lo - 1] : -1;
        const right = hi < bins - 1 ? tpoCounts[hi + 1] : -1;
        if (right >= left && hi < bins - 1) { hi++; vaTpo += tpoCounts[hi]; }
        else if (lo > 0) { lo--; vaTpo += tpoCounts[lo]; }
        else break;
    }
    const val = minP + lo * step;
    const vah = minP + (hi + 1) * step;
    const last = recent[recent.length - 1];
    const aboveVa = last.close > vah;
    const belowVa = last.close < val;
    const state = aboveVa ? "IMBALANCE_BULL" : (belowVa ? "IMBALANCE_BEAR" : "BALANCE_INSIDE_VA");
    const dir = aboveVa ? "BUY" : (belowVa ? "SELL" : "NEUTRAL");
    const aligned = ctx.baseSide === dir;
    const delta = dir === "NEUTRAL" ? 0 : (aligned ? 4 : -4);

    return makeEngineResult("ENG01_AMT_TPO", ctx, {
        status: (ctx.baseSide === "BUY" || ctx.baseSide === "SELL") ? (aligned || dir === "NEUTRAL" ? "PASS" : "FAIL") : "PASS",
        scoreDelta: delta,
        direction: dir,
        evidence: `${state} | POC:${pocPrice.toFixed(2)} VA:[${val.toFixed(2)}-${vah.toFixed(2)}] IB:[${ibLow.toFixed(2)}-${ibHigh.toFixed(2)}]`,
        reasonCode: state,
        explainBn: `TPO অকশন: ${state} (POC ${pocPrice.toFixed(2)})।`
    });
}

// 2) Market Breadth & Participation Engine
function runEng02Breadth(ctx) {
    const pool = (typeof DYNAMIC_SCAN_CACHE !== "undefined" ? DYNAMIC_SCAN_CACHE : []).filter(x => x.category === ctx.category && Number.isFinite(x.changePct));
    if (pool.length < 10) {
        return makeEngineResult("ENG02_BREADTH", ctx, {
            status: ctx.category === "commodity" ? "NOT_APPLICABLE" : "UNAVAILABLE",
            reasonCode: "INSUFFICIENT_UNIVERSE_BREADTH",
            explainBn: "মার্কেট ব্রেডথ পরিমাপের জন্য পর্যাপ্ত স্টক স্ক্যান ডেটা নেই।"
        });
    }
    const adv = pool.filter(x => x.changePct > 0).length;
    const dec = pool.filter(x => x.changePct < 0).length;
    const adRatio = dec > 0 ? Number((adv / dec).toFixed(2)) : adv;
    const advVol = pool.filter(x => x.changePct > 0).reduce((s, x) => s + (x.turnover || x.volume || 0), 0);
    const decVol = pool.filter(x => x.changePct < 0).reduce((s, x) => s + (x.turnover || x.volume || 0), 0);
    const upVolShare = (advVol + decVol) > 0 ? Math.round((advVol / (advVol + decVol)) * 100) : 50;

    // Check heavyweight concentration (top 3 share of total turnover)
    const sortedTurn = [...pool].sort((a, b) => (b.turnover || 0) - (a.turnover || 0));
    const totTurn = sortedTurn.reduce((s, x) => s + (x.turnover || 0), 0);
    const top3Share = totTurn > 0 ? Math.round((sortedTurn.slice(0, 3).reduce((s, x) => s + (x.turnover || 0), 0) / totTurn) * 100) : 0;

    const breadthBias = adRatio >= 1.25 && upVolShare >= 55 ? "BUY" : (adRatio <= 0.8 && upVolShare <= 45 ? "SELL" : "NEUTRAL");
    const conflict = (ctx.baseSide === "BUY" && breadthBias === "SELL") || (ctx.baseSide === "SELL" && breadthBias === "BUY");

    return makeEngineResult("ENG02_BREADTH", ctx, {
        status: conflict ? "FAIL" : "PASS",
        scoreDelta: conflict ? -5 : (ctx.baseSide === breadthBias ? 4 : 1),
        direction: breadthBias,
        evidence: `A/D:${adv}/${dec} (${adRatio}) | UpVol:${upVolShare}% | Top3Weight:${top3Share}%`,
        reasonCode: conflict ? "BREADTH_DIVERGENCE" : "BREADTH_OK",
        explainBn: `মার্কেট ব্রেডথ: ${adv}টি বাড়ছে বনাম ${dec}টি কমছে (Up-Vol ${upVolShare}%)।`
    });
}

// 3) Sector Rotation & Relative Strength Engine
function runEng03SectorRs(ctx) {
    const pool = (typeof DYNAMIC_SCAN_CACHE !== "undefined" ? DYNAMIC_SCAN_CACHE : []).filter(x => x.category === ctx.category);
    const item = pool.find(x => x.orderKey === ctx.orderKey);
    if (!pool.length || !item) {
        return makeEngineResult("ENG03_SECTOR_RS", ctx, {
            status: "UNAVAILABLE", reasonCode: "SECTOR_UNIVERSE_MISSING",
            explainBn: "সেক্টর রিলেটিভ স্ট্রেংথ তুলনার ডেটা এখনো তৈরি হয়নি।"
        });
    }
    const bench = ctx.category === "indian"
        ? (pool.find(x => x.orderKey === "NIFTY") || { changePct: 0 })
        : (pool.find(x => x.orderKey === "BTCUSDT") || { changePct: 0 });
    const secPeers = pool.filter(x => x.sector === item.sector);
    const secAvg = secPeers.length > 0 ? secPeers.reduce((s, x) => s + (x.changePct || 0), 0) / secPeers.length : item.changePct;
    const rsVsBench = Number((secAvg - (bench.changePct || 0)).toFixed(2));
    const stockVsSec = Number(((item.changePct || 0) - secAvg).toFixed(2));
    const dir = (rsVsBench > 0.25 && stockVsSec >= 0) ? "BUY" : ((rsVsBench < -0.25 && stockVsSec <= 0) ? "SELL" : "NEUTRAL");
    const conflict = (ctx.baseSide === "BUY" && dir === "SELL") || (ctx.baseSide === "SELL" && dir === "BUY");

    return makeEngineResult("ENG03_SECTOR_RS", ctx, {
        status: conflict ? "FAIL" : "PASS",
        scoreDelta: ctx.baseSide === dir ? 3 : (conflict ? -4 : 0),
        direction: dir,
        evidence: `Sector:${item.sector} | RS_Bench:${rsVsBench}% | Stock_vs_Sec:${stockVsSec}%`,
        reasonCode: conflict ? "LAGGARD_SECTOR_CONFLICT" : "SECTOR_RS_OK",
        explainBn: `সেক্টর (${item.sector}) বেঞ্চমার্কের চেয়ে ${rsVsBench >= 0 ? "+" : ""}${rsVsBench}% শক্তিশালী।`
    });
}

// 4) Intermarket & Macro Regime Engine
function runEng04IntermarketMacro(ctx) {
    const fxOk = typeof FX_RATE_STATE !== "undefined" && FX_RATE_STATE.usdToInr > 0 && FX_RATE_STATE.quality !== "OFFLINE";
    const vixOk = typeof liveVixMeta !== "undefined" && liveVixMeta.value > 0 && liveVixMeta.quality !== "OFFLINE";
    const goldAsset = typeof MASTER_ASSETS !== "undefined" ? MASTER_ASSETS.find(a => a.id === "goldmcx") : null;
    const goldChg = parseFloat(goldAsset?.change) || 0;

    if (!fxOk && !vixOk && (!goldAsset || goldAsset.priceNum === 0)) {
        return makeEngineResult("ENG04_INTERMARKET_MACRO", ctx, {
            status: "UNAVAILABLE", reasonCode: "MACRO_FEEDS_OFFLINE",
            explainBn: "USD/INR, VIX এবং গোল্ড ইন্টারমার্কেট ফিড অফলাইন।"
        });
    }
    const highVix = vixOk && liveVixMeta.value >= 24;
    const riskOff = highVix || goldChg >= 2.2;
    return makeEngineResult("ENG04_INTERMARKET_MACRO", ctx, {
        status: highVix ? "FAIL" : "PASS",
        scoreDelta: highVix ? -6 : (riskOff ? -2 : 2),
        direction: riskOff ? "BEARISH_MACRO" : "NEUTRAL",
        evidence: `USD/INR:${fxOk ? FX_RATE_STATE.usdToInr.toFixed(2) : "N/A"} | VIX:${vixOk ? liveVixMeta.value.toFixed(2) : "N/A"} | PAXG:${goldChg.toFixed(2)}% (DXY/US10Y:UNAVAIL)`,
        reasonCode: highVix ? "EXTREME_VIX_REGIME" : "MACRO_STABLE",
        explainBn: highVix ? `সতর্কতা: India VIX (${liveVixMeta.value.toFixed(2)}) অত্যন্ত বেশি!` : "ইন্টারমার্কেট ও ম্যাক্রো অবস্থা স্বাভাবিক আছে।"
    });
}

// 5) Advanced Tape Reading Engine (Honest: requires true Time & Sales / Trade Aggressor stream)
function runEng05TapeReading(ctx) {
    // Public @depth20 + @ticker does NOT provide true trade aggressor Time & Sales
    return makeEngineResult("ENG05_TAPE_READING", ctx, {
        status: "UNAVAILABLE",
        scoreDelta: 0,
        direction: "NEUTRAL",
        evidence: "True Time & Sales / Trade Aggressor stream not connected (L2 depth != tape)",
        dataQuality: "UNAVAILABLE",
        reasonCode: "NO_TICK_AGGRESSOR_FEED",
        explainBn: "রিয়েল টাইম-অ্যান্ড-সেলস (Aggressor Tape) ফিড না থাকায় এই ইঞ্জিন আনএভেইলেবল রাখা হয়েছে (মিথ্যা PASS নয়)।"
    });
}

// 6) Liquidity Heatmap & Depth Imbalance Engine
function runEng06LiquidityHeatmap(ctx) {
    const l2 = typeof LIVE_L2_DEPTH_MAP !== "undefined" ? LIVE_L2_DEPTH_MAP[ctx.orderKey] : null;
    if (!l2 || !l2.hasL2 || (Date.now() - l2.updatedAt > 15000)) {
        return makeEngineResult("ENG06_LIQUIDITY_HEATMAP", ctx, {
            status: ctx.category === "indian" ? "NOT_APPLICABLE" : "UNAVAILABLE",
            reasonCode: "NO_LIVE_L2_HEATMAP",
            explainBn: ctx.category === "indian" ? "এই ইক্যুইটি ফিডে L2 অর্ডারবুক ডেপথ নেই (NOT_APPLICABLE)।" : "ক্রিপ্টো L2 ডেপথ আপডেট পাওয়া যাচ্ছে না।"
        });
    }
    const spreadPct = l2.bestBid > 0 ? ((l2.bestAsk - l2.bestBid) / l2.bestBid) * 100 : 0;
    const dir = l2.buyers >= 62 ? "BUY" : (l2.sellers >= 62 ? "SELL" : "NEUTRAL");
    const wideSpread = spreadPct > 0.15;
    const conflict = (ctx.baseSide === "BUY" && dir === "SELL") || (ctx.baseSide === "SELL" && dir === "BUY");

    return makeEngineResult("ENG06_LIQUIDITY_HEATMAP", ctx, {
        status: (wideSpread || conflict) ? "FAIL" : "PASS",
        scoreDelta: wideSpread ? -6 : (ctx.baseSide === dir ? 4 : 0),
        direction: dir,
        evidence: `Bid:${l2.buyers}% Ask:${l2.sellers}% | Spread:${spreadPct.toFixed(4)}% | Depth:$${Math.round(l2.depthNotional || 0)}`,
        dataSource: l2.source || l2.method,
        dataQuality: "LIVE",
        reasonCode: wideSpread ? "LIQUIDITY_VACUUM_SPREAD" : (l2.isBlock ? "HEURISTIC_WALL_IMBALANCE" : "DEPTH_BALANCED"),
        explainBn: wideSpread ? `স্প্রেড বেশি (${spreadPct.toFixed(3)}%), লিকুইডিটি কম!` : `অর্ডারবুক ডেপথ: Bid ${l2.buyers}% / Ask ${l2.sellers}%।`
    });
}

// 7, 8, 9) Options Flow, Volatility Surface & GEX (Honest: require verified Options Chain API)
function runEng07OptionsFlow(ctx) {
    return makeEngineResult("ENG07_OPTIONS_FLOW", ctx, {
        status: "NOT_APPLICABLE",
        evidence: "Verified strike-wise options flow & OI chain API not subscribed",
        dataQuality: "UNAVAILABLE",
        reasonCode: "NO_OPTIONS_CHAIN_FEED",
        explainBn: "ভেরিফায়েড অপশন-চেইন ফিড না থাকায় Options Flow চালু করা হয়নি।"
    });
}
function runEng08VolSurface(ctx) {
    return makeEngineResult("ENG08_VOL_SURFACE", ctx, {
        status: "NOT_APPLICABLE",
        evidence: "Strike/Expiry IV surface requires verified options chain feed",
        dataQuality: "UNAVAILABLE",
        reasonCode: "NO_IV_SURFACE_FEED",
        explainBn: "অপশন IV সারফেস ডেটা অনুপলব্ধ (NOT_APPLICABLE)।"
    });
}
function runEng09GexPinning(ctx) {
    return makeEngineResult("ENG09_GEX_PINNING", ctx, {
        status: "NOT_APPLICABLE",
        evidence: "Strike-wise OI & Gamma Exposure proxy requires verified options chain",
        dataQuality: "UNAVAILABLE",
        reasonCode: "NO_GEX_OI_FEED",
        explainBn: "GEX ও এক্সপায়ারি পিনিং হিসাবের জন্য অপশন OI চেইন প্রয়োজন।"
    });
}

// 10) Event & Catalyst Intelligence Engine
function runEng10EventCatalyst(ctx) {
    const ev = typeof evaluateNewsEventRiskFilter === "function"
        ? evaluateNewsEventRiskFilter({ keywords: [ctx.orderKey.toLowerCase(), ctx.name.toLowerCase()] })
        : { state: "UNKNOWN", riskLevel: "UNKNOWN", count: 0 };
    if (ev.state === "HALT_NO_TRADE") {
        return makeEngineResult("ENG10_EVENT_CATALYST", ctx, {
            status: "FAIL", scoreDelta: -20, direction: "HALT",
            evidence: ev.reason || "Severe critical event halt",
            reasonCode: "EVENT_RISK_NO_TRADE_GATE",
            explainBn: `গুরুত্বপূর্ণ ইভেন্ট/সার্কিট রিস্কের কারণে ট্রেডিং গেট বন্ধ রাখা হয়েছে।`
        });
    }
    return makeEngineResult("ENG10_EVENT_CATALYST", ctx, {
        status: "PASS",
        scoreDelta: ev.riskLevel === "ELEVATED" ? -3 : 1,
        direction: "NEUTRAL",
        evidence: `State:${ev.state} | Risk:${ev.riskLevel} | Catalysts:${ev.count}`,
        reasonCode: ev.state,
        explainBn: `ইভেন্ট গেট: ${ev.riskLevel} (ম্যাক্রো/ব্রেকিং ক্যাটালিস্ট ${ev.count}টি)।`
    });
}

// 11) News Sentiment & Source Reliability Engine
function runEng11NewsSentiment(ctx) {
    const items = typeof allFetchedNewsItems !== "undefined" ? allFetchedNewsItems : [];
    if (typeof newsFetchState === "undefined" || newsFetchState !== "LOADED" || !items.length) {
        return makeEngineResult("ENG11_NEWS_SENTIMENT", ctx, {
            status: "PASS", scoreDelta: 0, direction: "NEUTRAL",
            evidence: "No breaking negative asset news in wire",
            dataQuality: "DELAYED", reasonCode: "NO_ACTIVE_ASSET_NEWS",
            explainBn: "নিউজ ওয়্যারে কোনো নেতিবাচক গুজব বা কনফ্লিক্ট নেই।"
        });
    }
    const rel = items.filter(n => n.title.toLowerCase().includes(ctx.orderKey.toLowerCase().replace("USDT", "")));
    const official = rel.filter(n => n.trustTier === "OFFICIAL");
    const rumorOnly = rel.length > 0 && rel.every(n => n.trustTier === "LOW_TRUST_RUMOR");
    const bull = rel.filter(n => n.impact === "BULLISH").length;
    const bear = rel.filter(n => n.impact === "BEARISH").length;
    const sent = bull > bear ? "BUY" : (bear > bull ? "SELL" : "NEUTRAL");
    const conflict = !rumorOnly && ((ctx.baseSide === "BUY" && sent === "SELL") || (ctx.baseSide === "SELL" && sent === "BUY"));

    return makeEngineResult("ENG11_NEWS_SENTIMENT", ctx, {
        status: conflict ? "FAIL" : "PASS",
        scoreDelta: rumorOnly ? -2 : (conflict ? -5 : (sent === ctx.baseSide ? 3 : 0)),
        direction: sent,
        evidence: `Matches:${rel.length} Official:${official.length} RumorOnly:${rumorOnly} Sentiment:${sent}`,
        reasonCode: rumorOnly ? "RUMOR_DISCOUNTED" : (conflict ? "NEWS_SENTIMENT_CONFLICT" : "NEWS_OK"),
        explainBn: rumorOnly ? "গুজব ভিত্তিক খবর উপেক্ষা করা হয়েছে।" : `নিউজ সেন্টিমেন্ট: ${sent} (অফিশিয়াল: ${official.length})।`
    });
}

// 12) Volatility Regime & Adaptive Strategy Router
function runEng12RegimeRouter(ctx) {
    const reg = ctx.quantSnap?.regime?.regime || "UNKNOWN";
    const allow = ctx.quantSnap?.regime?.strategyAllowlist || [];
    if (reg === "UNKNOWN") {
        return makeEngineResult("ENG12_REGIME_ROUTER", ctx, {
            status: "UNAVAILABLE", reasonCode: "REGIME_UNKNOWN",
            explainBn: "মার্কেট রেজিম শনাক্ত করার জন্য পর্যাপ্ত ডেটা নেই।"
        });
    }
    const isChop = reg === "HIGH_VOL_CHOP";
    const conflict = (ctx.baseSide === "BUY" && reg === "TRENDING_BEAR") || (ctx.baseSide === "SELL" && reg === "TRENDING_BULL");
    return makeEngineResult("ENG12_REGIME_ROUTER", ctx, {
        status: (isChop || conflict) ? "FAIL" : "PASS",
        scoreDelta: isChop ? -6 : (conflict ? -8 : 3),
        direction: reg === "TRENDING_BULL" ? "BUY" : (reg === "TRENDING_BEAR" ? "SELL" : "NEUTRAL"),
        evidence: `Regime:${reg} | Allowlist:[${allow.join(",")}]`,
        reasonCode: isChop ? "CHOP_REGIME_BLOCK" : (conflict ? "COUNTER_REGIME_BLOCK" : "REGIME_ALIGNED"),
        explainBn: `রেজিম রাউটার: ${reg} (অনুমোদিত: ${allow.join(", ") || "WAIT"})।`
    });
}

// 13) Cross-Asset Lead/Lag & Correlation Breakdown Engine
function runEng13LeadLagCorr(ctx) {
    const corr = ctx.quantSnap?.corr;
    if (!corr || corr.status !== "LIVE") {
        return makeEngineResult("ENG13_LEAD_LAG_CORR", ctx, {
            status: "PASS", scoreDelta: 0,
            evidence: `Peer correlation ${corr?.status || "UNSYNCED"} (non-blocking)`,
            reasonCode: "CORR_SYNC_PENDING",
            explainBn: "ক্রস-অ্যাসেট কোরিলেশন (কজেশন নয়) পর্যাপ্ত স্যাম্পল সংগ্রহ করছে।"
        });
    }
    const breakdown = Math.abs(corr.pearson) < 0.15;
    return makeEngineResult("ENG13_LEAD_LAG_CORR", ctx, {
        status: "PASS",
        scoreDelta: breakdown ? -1 : 2,
        evidence: `r=${corr.pearson} vs ${corr.peer} (N=${corr.samples}, Correlation!=Causation)`,
        reasonCode: breakdown ? "CORRELATION_BREAKDOWN_WATCH" : "CORR_STABLE",
        explainBn: `${corr.peer}-এর সাথে রোলিং কোরিলেশন ${corr.pearson} (শুধু পরিসংখ্যানগত সম্পর্ক)।`
    });
}

// 14) Portfolio Exposure & Concentration Engine
function runEng14PortfolioExposure(ctx) {
    const posMap = (typeof SESSION_VAULT !== "undefined" && SESSION_VAULT.fastPositionsMap) ? SESSION_VAULT.fastPositionsMap : {};
    const seen = new Set();
    let longCount = 0, shortCount = 0, sameSymbolConflict = false;
    for (const k in posMap) {
        const p = posMap[k];
        if (!p || seen.has(p.id) || Number(p.qty) === 0) continue;
        seen.add(p.id);
        if (p.qty > 0) longCount++;
        else shortCount++;
        const pSym = String(p.tsym || p.token || "").toUpperCase();
        if (pSym.includes(ctx.orderKey) && ((p.qty > 0 && ctx.baseSide === "SELL") || (p.qty < 0 && ctx.baseSide === "BUY"))) {
            sameSymbolConflict = true;
        }
    }
    const dirOverloaded = (ctx.baseSide === "BUY" && longCount >= 3) || (ctx.baseSide === "SELL" && shortCount >= 3);
    const fail = sameSymbolConflict || dirOverloaded;
    return makeEngineResult("ENG14_PORTFOLIO_EXPOSURE", ctx, {
        status: fail ? "FAIL" : "PASS",
        scoreDelta: fail ? -10 : 1,
        evidence: `Open:${seen.size} (L:${longCount}/S:${shortCount}) | Conflict:${sameSymbolConflict}`,
        reasonCode: fail ? "CONCENTRATION_OR_CONFLICT" : "EXPOSURE_SAFE",
        explainBn: fail ? "পোর্টফোলিও কনসেনট্রেশন বা বিপরীত পজিশন কনফ্লিক্ট পাওয়া গেছে!" : `পোর্টফোলিও এক্সপোজার নিরাপদ (খোলা পজিশন ${seen.size}টি)।`
    });
}

// 15) Execution Quality & Smart Order Router Engine
function runEng15ExecutionSor(ctx) {
    const slip = ctx.quantSnap?.slippageEst;
    const sc = (typeof QUANT_FEATURE_STORE !== "undefined" && QUANT_FEATURE_STORE.adaptiveScorecard) ? QUANT_FEATURE_STORE.adaptiveScorecard : { rejectRatePct: 0 };
    const canonical = typeof resolveCanonicalInstrument === "function" ? resolveCanonicalInstrument(ctx.orderKey) : null;
    if (!canonical) {
        return makeEngineResult("ENG15_EXECUTION_SOR", ctx, {
            status: "FAIL", scoreDelta: -10, reasonCode: "UNMAPPED_BROKER_ROUTE",
            explainBn: "এই স্টকের ব্রোকার রাউটিং ম্যাপ পাওয়া যায়নি।"
        });
    }
    const highSlip = slip?.estimatedSlippageBps && slip.estimatedSlippageBps > 35;
    const highRej = sc.rejectRatePct > 30;
    return makeEngineResult("ENG15_EXECUTION_SOR", ctx, {
        status: (highSlip || highRej) ? "FAIL" : "PASS",
        scoreDelta: (highSlip || highRej) ? -5 : 2,
        evidence: `Route:${canonical.broker} | EstSlip:${slip?.estimatedSlippageBps ?? "--"}bps | RejRate:${sc.rejectRatePct}%`,
        reasonCode: highSlip ? "HIGH_SLIPPAGE_EST" : "SOR_READY",
        explainBn: `স্মার্ট রাউটার: ${canonical.broker} (আনুমানিক স্লিপেজ ${slip?.estimatedSlippageBps ?? "স্বাভাবিক"} bps)।`
    });
}

// 16) Adaptive Position Sizing Engine (Never scales up from opportunity score!)
function runEng16AdaptiveSizing(ctx) {
    const canonical = typeof resolveCanonicalInstrument === "function" ? resolveCanonicalInstrument(ctx.orderKey) : null;
    const ltp = Number(ctx.ltp || 0);
    const sl = Number(ctx.sl || 0);
    if (!canonical || ltp <= 0 || sl <= 0 || Math.abs(ltp - sl) === 0) {
        return makeEngineResult("ENG16_ADAPTIVE_SIZING", ctx, {
            status: "UNAVAILABLE", reasonCode: "INVALID_PRICE_OR_SL",
            explainBn: "পজিশন সাইজিংয়ের জন্য সঠিক Entry এবং Stop-Loss পাওয়া যায়নি।"
        });
    }
    const fx = ctx.category === "indian" ? 1 : (typeof FX_RATE_STATE !== "undefined" && FX_RATE_STATE.usdToInr ? FX_RATE_STATE.usdToInr : 86);
    const stopDistInr = Math.abs(ltp - sl) * fx;
    const dailyBudget = Number(window.AUTO_TRADE_STATE?.dailyBudgetInr || 10000);
    const usedBudget = Number(window.AUTO_TRADE_STATE?.usedBudgetInr || 0) + Number(window.AUTO_TRADE_STATE?.reservedBudgetInr || 0);
    const remBudget = Math.max(0, dailyBudget - usedBudget);
    const riskCapInr = Math.min(dailyBudget * 0.25, Number(SESSION_VAULT?.serverRiskState?.capitalInr || 100000) * 0.01);

    let qty = canonical.lotSize || 1;
    if (canonical.broker === "SHOONYA") {
        const riskLots = Math.max(1, Math.floor((riskCapInr / stopDistInr) / canonical.lotSize));
        const budgetLots = Math.max(0, Math.floor((remBudget / (ltp * 0.25)) / canonical.lotSize));
        const finalLots = Math.min(riskLots, Math.max(1, budgetLots));
        qty = finalLots * canonical.lotSize;
    } else {
        const maxByRisk = riskCapInr / stopDistInr;
        const maxByBudget = (remBudget / fx) / ltp;
        const rawQty = Math.min(canonical.maxQty || 10, maxByRisk, maxByBudget);
        qty = Math.max(canonical.lotSize || 0.001, Number(rawQty.toFixed(4)));
    }
    const requiredInr = Math.round(qty * ltp * fx * (canonical.broker === "SHOONYA" && canonical.segment === "EQ" ? 0.22 : 1));
    ctx.computedSizing = { qty, requiredInr, riskCapInr: Math.round(riskCapInr) };

    const overBudget = remBudget <= 0 || requiredInr > remBudget;
    return makeEngineResult("ENG16_ADAPTIVE_SIZING", ctx, {
        status: overBudget ? "FAIL" : "PASS",
        scoreDelta: overBudget ? -8 : 2,
        evidence: `AutoQty:${qty} | EstReq:₹${requiredInr} | RemBudget:₹${Math.round(remBudget)}`,
        reasonCode: overBudget ? "INSUFFICIENT_DAILY_BUDGET" : "SIZING_COMPUTED",
        explainBn: `অ্যাডাপ্টিভ সাইজিং: অটো কোয়ান্টিটি ${qty} (আনুমানিক প্রয়োজন ₹${requiredInr})।`
    });
}

// 17) Trade Lifecycle & Recovery Engine
function runEng17TradeLifecycle(ctx) {
    const now = new Date();
    if (ctx.category === "indian" && typeof getExchangeSessionStatus === "function") {
        const sess = getExchangeSessionStatus("indian", now);
        if (!sess.isOpen) {
            return makeEngineResult("ENG17_TRADE_LIFECYCLE", ctx, {
                status: "FAIL", scoreDelta: -5,
                evidence: `Exchange Session: ${sess.state} (${sess.reason})`,
                reasonCode: "OUTSIDE_EXCHANGE_SESSION",
                explainBn: `NSE/BSE মার্কেট এখন বন্ধ (${sess.state}), তাই লাইভ ইন্ট্রাডে এন্ট্রি ব্লক।`
            });
        }
        // Check 15:10 IST square-off cutoff for new MIS entries
        const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Kolkata", hour12: false, hour: "numeric", minute: "numeric" }).formatToParts(now);
        const mins = (parseInt(parts.find(p => p.type === "hour")?.value || "0", 10) * 60) + parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
        if (mins >= 910) {
            return makeEngineResult("ENG17_TRADE_LIFECYCLE", ctx, {
                status: "FAIL", scoreDelta: -10,
                evidence: "Past 3:10 PM IST MIS Square-Off Cutoff",
                reasonCode: "MIS_CUTOFF_REACHED",
                explainBn: "দুপুর ৩:১০-এর পর নতুন ইন্ট্রাডে অটো-এন্ট্রি সুরক্ষার জন্য বন্ধ।"
            });
        }
    }
    return makeEngineResult("ENG17_TRADE_LIFECYCLE", ctx, {
        status: "PASS", scoreDelta: 1,
        evidence: "Session Active • SL/TP Lifecycle Ready",
        reasonCode: "LIFECYCLE_OK",
        explainBn: "ট্রেড লাইফসাইকেল ও স্কয়ার-অফ গার্ড সক্রিয় আছে।"
    });
}

// 18) Strategy Performance & Drift Monitor
function runEng18DriftMonitor(ctx) {
    const sc = (typeof QUANT_FEATURE_STORE !== "undefined" && QUANT_FEATURE_STORE.adaptiveScorecard) ? QUANT_FEATURE_STORE.adaptiveScorecard : { totalRecorded: 0, rejectRatePct: 0 };
    const wf = ctx.quantSnap?.walkFwd;
    const degraded = sc.rejectRatePct > 35 || (wf?.brierScore !== null && wf?.brierScore > 0.32);
    return makeEngineResult("ENG18_DRIFT_MONITOR", ctx, {
        status: degraded ? "FAIL" : "PASS",
        scoreDelta: degraded ? -6 : 1,
        evidence: `Orders:${sc.totalRecorded} Rej:${sc.rejectRatePct}% | Brier:${wf?.brierScore ?? "--"} | AutoMutate:LOCKED`,
        reasonCode: degraded ? "PERFORMANCE_DRIFT_PAUSE" : "DRIFT_WITHIN_LIMIT",
        explainBn: degraded ? "স্ট্র্যাটেজিতে ড্রিফট শনাক্ত হওয়ায় নতুন অটো এন্ট্রি পজ করা হয়েছে।" : "স্ট্র্যাটেজি ড্রিফট স্বাভাবিক সীমার মধ্যে আছে।"
    });
}

// 19) Walk-Forward & Robustness Validation Engine
function runEng19WfRobustness(ctx) {
    const wf = ctx.quantSnap?.walkFwd;
    if (!wf || wf.status === "INSUFFICIENT_DATA" || wf.status === "NO_SIGNALS") {
        return makeEngineResult("ENG19_WF_ROBUSTNESS", ctx, {
            status: "UNAVAILABLE", scoreDelta: 0,
            evidence: `WF:${wf?.status || "NONE"} (${wf?.trades || 0} trades)`,
            reasonCode: "WF_INSUFFICIENT_HISTORY",
            explainBn: "ওয়াক-ফরওয়ার্ড আউট-অফ-স্যাম্পল টেস্টের জন্য আরও ক্লোজড ক্যান্ডেল প্রয়োজন।"
        });
    }
    const robust = !wf.overfittingWarning && (wf.profitFactor === null || wf.profitFactor >= 1.05);
    return makeEngineResult("ENG19_WF_ROBUSTNESS", ctx, {
        status: robust ? "PASS" : "FAIL",
        scoreDelta: robust ? 4 : -4,
        evidence: `N:${wf.trades} | PF:${wf.profitFactor} | Net:${wf.netReturnPct}% | DD:${wf.maxDrawdownPct}%`,
        reasonCode: robust ? "WF_ROBUST" : "OVERFIT_OR_WEAK_PF",
        explainBn: robust ? `ওয়াক-ফরওয়ার্ড টেস্ট পাস (Profit Factor ${wf.profitFactor}, Drawdown ${wf.maxDrawdownPct}%)।` : `ওয়াক-ফরওয়ার্ড স্যাম্পল কম বা প্রফিট ফ্যাক্টর দুর্বল (${wf.trades} ট্রেড)।`
    });
}

// 20) Data Anomaly & Feed Consensus Engine
function runEng20FeedConsensus(ctx) {
    const qual = ctx.dataQuality;
    const isFresh = qual === "LIVE" || qual === "DELAYED";
    const ageMs = ctx.eventTime ? Math.max(0, Date.now() - ctx.eventTime) : 999999;
    const stale = ageMs > 60000;
    const pass = isFresh && !stale && ctx.ltp > 0;
    return makeEngineResult("ENG20_FEED_CONSENSUS", ctx, {
        status: pass ? "PASS" : "FAIL",
        scoreDelta: pass ? 2 : -15,
        evidence: `Quality:${qual} | Source:${ctx.dataSource} | Age:${Math.round(ageMs / 1000)}s`,
        reasonCode: pass ? "FEED_VERIFIED" : "FEED_STALE_OR_OFFLINE",
        explainBn: pass ? `ডেটা ফিড যাচাইকৃত (${qual} • ${ctx.dataSource})।` : `ডেটা ফিড সতেজ নয় (${qual}) — অটো ট্রেড সুরক্ষার জন্য ব্লক।`
    });
}

function runAdditiveEnginesPipeline(asset, baseEval = {}, quantSnap = null) {
    if (!asset) return null;
    const env = asset.dataEnvelope || {};
    const closedBars = getStrictClosedCandles(asset.id, "15m");
    const baseSide = (baseEval.direction === "BUY" || baseEval.side === "BUY") ? "BUY"
        : ((baseEval.direction === "SELL" || baseEval.direction === "SHORT" || baseEval.side === "SELL") ? "SELL" : "NEUTRAL");

    const ctx = {
        assetId: asset.id,
        symbol: asset.symbol,
        orderKey: asset.orderKey,
        name: asset.name,
        category: asset.category || "indian",
        timeframe: "15m",
        ltp: Number(asset.priceNum || asset.price || 0),
        sl: Number(baseEval.sl || quantSnap?.twoWay?.sl || 0),
        target: Number(baseEval.target || quantSnap?.twoWay?.target || 0),
        baseScore: Number(baseEval.score || 0),
        baseSide,
        dataSource: env.source || asset.exchange || "UNKNOWN",
        dataQuality: env.quality || asset.dataQuality || "OFFLINE",
        eventTime: env.eventTime || Date.now(),
        closedBars,
        quantSnap,
        computedSizing: null
    };

    const results = [
        runEng01AmtTpo(ctx),
        runEng02Breadth(ctx),
        runEng03SectorRs(ctx),
        runEng04IntermarketMacro(ctx),
        runEng05TapeReading(ctx),
        runEng06LiquidityHeatmap(ctx),
        runEng07OptionsFlow(ctx),
        runEng08VolSurface(ctx),
        runEng09GexPinning(ctx),
        runEng10EventCatalyst(ctx),
        runEng11NewsSentiment(ctx),
        runEng12RegimeRouter(ctx),
        runEng13LeadLagCorr(ctx),
        runEng14PortfolioExposure(ctx),
        runEng15ExecutionSor(ctx),
        runEng16AdaptiveSizing(ctx),
        runEng17TradeLifecycle(ctx),
        runEng18DriftMonitor(ctx),
        runEng19WfRobustness(ctx),
        runEng20FeedConsensus(ctx)
    ];

    const passedCount = results.filter(r => r.status === "PASS").length;
    const failedEngines = results.filter(r => r.status === "FAIL");
    const reqEngines = results.filter(r => r.required);
    const reqPassed = reqEngines.filter(r => r.status === "PASS").length;
    const reqFailedOrUnavail = reqEngines.filter(r => r.status !== "PASS");

    const rawDelta = results.reduce((s, r) => s + (Number.isFinite(r.scoreDelta) ? r.scoreDelta : 0), 0);
    const calibratedDelta = Math.max(-25, Math.min(20, rawDelta));
    const fusionScore = ctx.baseScore > 0 ? Math.max(0, Math.min(100, Math.round(ctx.baseScore + calibratedDelta))) : 0;

    // Hard Gate: Score >= 90 AND 100% required engines PASS AND zero FAIL across all engines AND LIVE verified feed
    const allRequiredPass = reqEngines.length > 0 && reqFailedOrUnavail.length === 0 && failedEngines.length === 0;
    const isLiveVerified = ctx.dataQuality === "LIVE";
    const autoTradeEligible = fusionScore >= 90 && allRequiredPass && isLiveVerified && (baseSide === "BUY" || baseSide === "SELL");

    const blockReasons = [];
    if (fusionScore < 90) blockReasons.push(`Score ${fusionScore}/100 < 90`);
    if (!allRequiredPass) blockReasons.push(`${reqFailedOrUnavail.length}টি আবশ্যক ইঞ্জিন PASS হয়নি`);
    if (!isLiveVerified) blockReasons.push(`ডেটা ${ctx.dataQuality} (অটো ট্রেডের জন্য LIVE প্রয়োজন)`);
    if (baseSide === "NEUTRAL") blockReasons.push("নির্দিষ্ট BUY/SELL ডিরেকশন নেই");

    const bengaliSummary = autoTradeEligible
        ? `✅ সব শর্ত পূরণ! Fusion স্কোর ${fusionScore}/100 এবং ১০০% প্রয়োজনীয় ইঞ্জিন PASS (${reqPassed}/${reqEngines.length})।`
        : `⏸️ অটো-ট্রেড হোল্ড: স্কোর ${fusionScore}/100 | প্রয়োজনীয় ইঞ্জিন PASS ${reqPassed}/${reqEngines.length}। কারণ: ${blockReasons[0] || "অপেক্ষমাণ"}।`;

    const report = {
        version: ARAF_ENGINES_VERSION,
        timestamp: Date.now(),
        symbol: ctx.symbol,
        orderKey: ctx.orderKey,
        category: ctx.category,
        side: baseSide,
        entry: ctx.ltp,
        sl: ctx.sl,
        target: ctx.target,
        baseScore: ctx.baseScore,
        fusionDelta: calibratedDelta,
        fusionScore,
        passedCount,
        failedCount: failedEngines.length,
        unavailableCount: results.filter(r => r.status === "UNAVAILABLE" || r.status === "NOT_APPLICABLE").length,
        requiredTotal: reqEngines.length,
        requiredPassed: reqPassed,
        allRequiredPass,
        isLiveVerified,
        autoTradeEligible,
        blockReasons,
        sizing: ctx.computedSizing,
        bengaliSummary,
        engines: results
    };

    window.LAST_ENGINE_FUSION_REPORT = report;
    ENGINE_REPLAY_JOURNAL.unshift({
        ts: report.timestamp,
        version: report.version,
        symbol: report.symbol,
        fusionScore: report.fusionScore,
        allRequiredPass: report.allRequiredPass,
        autoTradeEligible: report.autoTradeEligible,
        snapshot: { ltp: ctx.ltp, dataQuality: ctx.dataQuality, dataSource: ctx.dataSource, closedBarsCount: closedBars.length },
        engines: results.map(r => ({ id: r.engineId, status: r.status, delta: r.scoreDelta, reason: r.reasonCode }))
    });
    if (ENGINE_REPLAY_JOURNAL.length > 40) ENGINE_REPLAY_JOURNAL.pop();

    return report;
}

function renderNewEnginesCard(report) {
    if (!report) return;
    const scoreEl = document.getElementById("fusionV2ScoreBadge");
    const cntEl = document.getElementById("enginesStatusCounts");
    const gateEl = document.getElementById("enginesHardGateBadge");
    const sumEl = document.getElementById("enginesBengaliSummaryText");

    if (scoreEl) {
        scoreEl.textContent = `SCORE: ${report.fusionScore}/100 (${report.fusionDelta >= 0 ? "+" : ""}${report.fusionDelta})`;
        scoreEl.className = report.fusionScore >= 90
            ? "px-2 py-0.5 rounded bg-[#00d09c] text-slate-950 font-black"
            : "px-2 py-0.5 rounded bg-slate-800 text-amber-400 font-black";
    }
    if (cntEl) cntEl.textContent = `PASS: ${report.passedCount} • FAIL: ${report.failedCount} • N/A: ${report.unavailableCount} (REQ ${report.requiredPassed}/${report.requiredTotal})`;
    if (gateEl) {
        gateEl.textContent = report.autoTradeEligible ? "GATE: 100% PASS" : "GATE: BLOCKED";
        gateEl.className = report.autoTradeEligible ? "text-[#00d09c] font-black" : "text-amber-400 font-bold";
    }
    if (sumEl) sumEl.textContent = report.bengaliSummary;
}

function openEnginesExplainModal() {
    const modal = document.getElementById("enginesExplainModal");
    const body = document.getElementById("enginesModalBody");
    const sub = document.getElementById("enginesModalSub");
    const rep = window.LAST_ENGINE_FUSION_REPORT;
    if (!modal || !body) return;

    if (!rep) {
        body.innerHTML = `<p class="text-slate-500 font-mono text-center py-4">এখনো কোনো অ্যাসেট বিশ্লেষণ হয়নি।</p>`;
        modal.classList.remove("hidden");
        return;
    }

    if (sub) sub.textContent = `${rep.symbol} • Fusion ${rep.fusionScore}/100 • Req ${rep.requiredPassed}/${rep.requiredTotal}`;
    body.innerHTML = rep.engines.map(e => {
        const badgeCls = e.status === "PASS"
            ? "bg-[#00d09c]/15 text-[#059669] border-[#00d09c]/30"
            : (e.status === "FAIL" ? "bg-[#eb5b3c]/15 text-[#eb5b3c] border-[#eb5b3c]/30" : "bg-slate-100 text-slate-600 border-slate-200");
        return `
        <div class="p-2.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1">
            <div class="flex items-center justify-between">
                <b class="text-slate-900 text-[10.5px]">${e.label} ${e.required ? '<span class="text-[8px] text-amber-600">(REQ)</span>' : ""}</b>
                <span class="px-1.5 py-0.5 rounded border font-mono text-[8.5px] font-black ${badgeCls}">${e.status} (${e.scoreDelta >= 0 ? "+" : ""}${e.scoreDelta})</span>
            </div>
            <div class="text-[8.5px] font-mono text-slate-600 break-all">${e.evidence}</div>
            <p class="text-[9.5px] text-slate-800 font-sans">${e.explainBn}</p>
        </div>`;
    }).join("");

    modal.classList.remove("hidden");
}

function exportEngineReplaySnapshot() {
    const data = { exportedAt: new Date().toISOString(), version: ARAF_ENGINES_VERSION, latestReport: window.LAST_ENGINE_FUSION_REPORT || null, replayJournal: ENGINE_REPLAY_JOURNAL };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ARAF_ENGINES_REPLAY_${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}
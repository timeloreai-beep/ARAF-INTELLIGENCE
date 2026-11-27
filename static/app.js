/* ==========================================================================
   ARAF INTELLIGENCE — HARDENED INSTITUTIONAL ENGINE (app.js)
   Architecture: Zero-Trust Client • RAM X-Pin-Token • Bearer Auth
                 100ms Binance L2 WS Depth (Spot Ref Labeled)
                 Master Shoonya Relay & Unified PC Tunnel Support
                 Two-Way Intraday Engine (BUY / SHORT / CALL / PUT / NO TRADE)
                 EMA(9/21), RSI(14), 30m Candles, Protected 1-Click EXIT
                 Complete 10+8 Quant Engine • Walk-Forward Overfitting Guard
   ========================================================================== */

const FASTAPI_BASE_URL = (
    document.querySelector('meta[name="araf-api-base"]')?.content?.trim() || window.location.origin
).replace(/\/+$/, "");
const FASTAPI_TARGET_ROUTES = Object.freeze({
    health: "/health",
    news_feed: "/news/feed",
    ai_guardian: "/ai_guardian",
    get_risk_state: "/risk/state",
    get_master_user: "/auth/master-user",
    get_subscription_upi_intent: "/auth/subscription/upi-intent",
    submit_subscription_payment_proof: "/auth/subscription/payment-proof",
    save_master_user: "/auth/master-user",
    submit_kyc: "/auth/kyc",
    send_real_otp: "/auth/otp/send",
    verify_server_otp: "/auth/otp/verify",
    set_master_pin: "/auth/pin",
    verify_pin: "/auth/pin/verify",
    logout_session: "/auth/logout",
    login_with_trader_uid: "/auth/resolve-trader-uid",
    ipo_feed: "/ipo/feed",
    ipo_news_feed: "/ipo/news_feed",
    save_broker_vault: "/brokers/vault/save",
    get_broker_vault_status: "/brokers/vault/status",
    delete_broker_vault: "/brokers/vault/delete",
    shoonya_auto_login: "/brokers/shoonya/auto-login",
    shoonya_balance: "/brokers/balance/shoonya",
    coindcx_balance: "/brokers/balance/coindcx"
});
const FASTAPI_AUTH_FALLBACK_TARGETS = new Set([
    "get_master_user",
    "login_with_trader_uid"
]);
const LOCAL_TERMINAL_SETTINGS_KEY = "ARAF_TERMINAL_SETTINGS";
let terminalPreferences = {
    theme: "dark",
    accent: "mint",
    news_lang: "en-US",
    news_voice_digest_enabled: true,
    chart_timeframe: "15m"
};

const TERMINAL_EXECUTION_MODE = "PAPER";
const LIVE_AUTO_TRADING_ENABLED = false;

const firebaseConfig = {
    apiKey: "AIzaSyBGmdR_ElITZ8lNgJPjSMA-ksPjYQKddCc",
    authDomain: "araf-intelligence.firebaseapp.com",
    projectId: "araf-intelligence",
    storageBucket: "araf-intelligence.firebasestorage.app",
    messagingSenderId: "949905617207",
    appId: "1:949905617207:web:521009a47fb1766b012ab2"
};

if (!firebase.apps.length) {
    firebase.initializeApp(firebaseConfig);
}
const auth = firebase.auth();
const db = firebase.firestore();
auth.setPersistence(firebase.auth.Auth.Persistence.LOCAL).catch(e => console.warn("[Auth Persistence]:", e));

let liveIndiaVix = null;
let liveVixMeta = { value: null, source: null, updatedAt: 0, quality: "OFFLINE" };

let FX_RATE_STATE = {
    usdToInr: null,
    source: null,
    updatedAt: 0,
    quality: "OFFLINE"
};

let currentGreekOptionMode = "CE";
let activePositionBroker = "shoonya";
let activePositionSubTab = "open";
let activeHoldingsBroker = "shoonya";
let holdingsValuesVisible = true;
let newsVoiceDigestEnabled = true;

async function syncLiveUsdInrRate() {
    try {
        const res = await fetch("https://api.exchangerate-api.com/v4/latest/USD");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const rate = Number(data?.rates?.INR);
        if (Number.isFinite(rate) && rate > 0) {
            FX_RATE_STATE = {
                usdToInr: rate,
                source: "EXCHANGERATE_API",
                updatedAt: Date.now(),
                quality: "DERIVED"
            };
            return;
        }
        throw new Error("Invalid INR rate");
    } catch (e) {
        console.warn("[FX Sync]:", e.message);
        FX_RATE_STATE.quality = (FX_RATE_STATE.usdToInr && (Date.now() - FX_RATE_STATE.updatedAt < 3600000)) ? "STALE" : "OFFLINE";
    }
}

function getIndiaTradingDate() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}
const SUPPORTED_LANGUAGES = {
    "en-US": { name: "English", prefix: "Attention trader! " },
    "bn-IN": { name: "বাংলা", prefix: "প্রিয় ট্রেডার, একটু শুনবেন কি? " },
    "hi-IN": { name: "हिन्दी", prefix: "ध्यान दें! " },
    "gu-IN": { name: "ગુજરાતી", prefix: "ધ્યાન આપો! " },
    "mr-IN": { name: "मराठी", prefix: "लक्ष द्या! " },
    "ta-IN": { name: "தமிழ்", prefix: "கவனிக்கவும்! " }
};

const BUSINESS_PAYMENT_CONFIG = {
    monthlyPrice: 299,
    trialDays: 30
};
let subscriptionStatusPollTimer = null;

async function syncGlobalSubscriptionPrice() {
    try {
        const response = await fetch(`${FASTAPI_BASE_URL}/admin/ghost/pricing`, {
            cache: "no-store"
        });
        if (!response.ok) throw new Error(`Pricing request failed (${response.status})`);
        const data = await response.json();
        const price = Number(data.global_monthly_fee_inr);
        if (!Number.isFinite(price) || price < 0) {
            throw new Error("Server returned an invalid subscription price");
        }
        BUSINESS_PAYMENT_CONFIG.monthlyPrice = price;
        const priceEl = document.getElementById("displayDynamicSubPrice");
        if (priceEl) priceEl.textContent = `₹${price.toLocaleString("en-IN")}`;
        updateSubscriptionMonthTotal();
        return true;
    } catch (error) {
        console.warn("[Subscription Pricing]:", error);
        return false;
    }
}

void syncGlobalSubscriptionPrice();
window.setInterval(() => {
    if (!document.hidden) void syncGlobalSubscriptionPrice();
}, 30000);

const SESSION_VAULT = {
    userKey: null,
    activeUser: null,
    savedCloudUser: null,
    hasServerPin: false,
    pinSessionToken: null,
    shoonyaLinked: false,
    coindcxLinked: false,
    shoonyaStatus: "NOT_CONNECTED",
    coindcxStatus: "NOT_CONNECTED",
    voiceEnabled: true,
    voiceLang: "en-US",
    newsLang: "en-US",
    aiChatLang: "en-US",
    voiceIntervalMinutes: 3,
    currentOppData: null,
    tradeHistory: [],
    pendingOrdersList: [],
    dailyLossPnl: 0,
    serverRiskState: {
        isHardLocked: false,
        tradesCount: 0,
        dailyPnlInr: 0,
        capitalInr: 100000,
        maxDailyLossAmount: 2500,
        dailyLossLimitInr: -2500,
        maxTradesPerDay: 3
    },
    fastPositionsMap: {},
    paperAutomationPositions: [],
    localCandleHistory: {},
    activeBracketOrders: {},
    shoonyaLiveBalance: "Please connect your Shoonya API Key",
    coindcxLiveBalance: "0.00 USDT",
    coindcxUsdtNum: 0,
    shoonyaHoldingsList: [],
    coindcxHoldingsList: []
};

function getActiveDailyLossLimitInr() {
    const lim = Number(SESSION_VAULT.serverRiskState?.dailyLossLimitInr);
    return Number.isFinite(lim) && lim < 0 ? lim : -2500;
}

let activeCategory = "opportunities";

let currentActiveSymbol = "NSE:NIFTY";
let currentSelectedAsset = null;
let binanceWs = null;
let binanceReconnectTimer = null;
let binanceReconnectAttempts = 0;
let backendPriceWs = null;
let backendPriceWsReconnectTimer = null;
let backendPriceWsReconnectAttempts = 0;
let isServerOtpVerifiedThisSession = false;
let currentNiftyPrice = 0;
let activeNewsTab = "stock_only";
let allFetchedNewsItems = [];
let newsFetchState = "LOADING";
let isVoicePaused = false;
let isTerminalUnlocked = false;
let periodicVoiceIntervalId = null;
let marketPollIntervalId = null;
let freshnessIntervalId = null;
let sqOffIntervalId = null;
let deferredPrompt = null;
let liveIpoList = [];
let speechWatchdogTimer = null;
let lastUiUpdateTime = 0;
let lastQuantUiTime = 0;
let isOrderDispatching = false;
let isPnlBreachSyncInFlight = false;
let lastRiskPnlSyncAt = 0;
let riskPnlSyncFailed = false;
let serverRiskStateLoaded = false;
let serverAutoTradeStateLoaded = false;
let selectedCryptoLeverage = 1;

const LIVE_L2_DEPTH_MAP = {};

let NSE_TRADING_HOLIDAYS = [];

async function loadNseHolidays() {
    try {
        const res = await fastApiRequest("/market/holidays");
        if (res.ok && res.data) {
            const holidays = res.data;
            NSE_TRADING_HOLIDAYS = Object.values(holidays).flat();
        }
    } catch (e) {
        console.warn("Failed to load NSE holidays:", e);
    }
}

const CANONICAL_INSTRUMENTS = {
    NIFTY:      { broker: "SHOONYA", exchange: "NFO", segment: "FUT", lotSize: 65, tickSize: 0.05, isDerivative: true, supportsShort: true, token: "26000" },
    BANKNIFTY:  { broker: "SHOONYA", exchange: "NFO", segment: "FUT", lotSize: 30, tickSize: 0.05, isDerivative: true, supportsShort: true, token: "26009" },
    SENSEX:     { broker: "SHOONYA", exchange: "BFO", segment: "FUT", lotSize: 20, tickSize: 0.05, isDerivative: true, supportsShort: true, token: "1" },
    BANKEX:     { broker: "SHOONYA", exchange: "BFO", segment: "FUT", lotSize: 15, tickSize: 0.05, isDerivative: true, supportsShort: true, token: "12" },
    RELIANCE:   { broker: "SHOONYA", exchange: "NSE", segment: "EQ",  lotSize: 1,  tickSize: 0.05, isDerivative: false, supportsShort: true, token: "2885" },
    HDFCBANK:   { broker: "SHOONYA", exchange: "NSE", segment: "EQ",  lotSize: 1,  tickSize: 0.05, isDerivative: false, supportsShort: true, token: "1333" },
    TATAMOTORS: { broker: "SHOONYA", exchange: "NSE", segment: "EQ",  lotSize: 1,  tickSize: 0.05, isDerivative: false, supportsShort: true, token: "3456" },
    TCS:        { broker: "SHOONYA", exchange: "BSE", segment: "EQ",  lotSize: 1,  tickSize: 0.05, isDerivative: false, supportsShort: true, token: "532540" },
    INFY:       { broker: "SHOONYA", exchange: "BSE", segment: "EQ",  lotSize: 1,  tickSize: 0.05, isDerivative: false, supportsShort: true, token: "500209" },
    BTCUSDT:    { broker: "COINDCX", exchange: "COINDCX", segment: "FUTURES", lotSize: 0.001, tickSize: 0.1,  isDerivative: true, supportsShort: true, maxLeverage: 5, maxQty: 10, marketPair: "B-BTC_USDT" },
    ETHUSDT:    { broker: "COINDCX", exchange: "COINDCX", segment: "FUTURES", lotSize: 0.01,  tickSize: 0.01, isDerivative: true, supportsShort: true, maxLeverage: 5, maxQty: 100, marketPair: "B-ETH_USDT" },
    PAXGUSDT:   { broker: "COINDCX", exchange: "COINDCX", segment: "FUTURES", lotSize: 0.01,  tickSize: 0.01, isDerivative: true, supportsShort: true, maxLeverage: 5, maxQty: 30, marketPair: "B-PAXG_USDT" }
};

function resolveCanonicalInstrument(symbolKey) {
    if (!symbolKey) return null;
    const cleanKey = symbolKey.replace(/[^A-Z0-9&-]/gi, "").toUpperCase();
    if (CANONICAL_INSTRUMENTS[cleanKey]) return CANONICAL_INSTRUMENTS[cleanKey];
    if (/^[A-Z0-9]{2,15}USDT$/.test(cleanKey)) {
        const base = cleanKey.replace(/USDT$/, "");
        return { broker: "COINDCX", exchange: "COINDCX", segment: "FUTURES", lotSize: 0.01, tickSize: 0.01, isDerivative: true, supportsShort: true, maxLeverage: 5, maxQty: 500000, marketPair: `B-${base}_USDT` };
    }
    if (/^[A-Z0-9&-]{2,20}$/.test(cleanKey)) {
        return { broker: "SHOONYA", exchange: "NSE", segment: "EQ", lotSize: 1, tickSize: 0.05, isDerivative: false, supportsShort: true, token: "DYNAMIC" };
    }
    return null;
}

function matchCanonicalOrderKeyFromTsym(tsymUpper) {
    if (!tsymUpper) return null;
    const sortedKeys = Object.keys(CANONICAL_INSTRUMENTS).sort((a, b) => b.length - a.length);
    for (const k of sortedKeys) {
        if (tsymUpper.includes(k)) return k;
    }
    return null;
}

const EXCHANGE_LOT_SPECS = {
    NIFTY:      { lot: 65, step: 65, min: 65, leverage: "5X MIS (Indicative NSE Intraday)", type: "FNO", exchange: "NFO" },
    BANKNIFTY:  { lot: 30, step: 30, min: 30, leverage: "5X MIS (Indicative NSE Intraday)", type: "FNO", exchange: "NFO" },
    SENSEX:     { lot: 20, step: 20, min: 20, leverage: "5X MIS (Indicative BSE Intraday)", type: "FNO", exchange: "BFO" },
    BANKEX:     { lot: 15, step: 15, min: 15, leverage: "5X MIS (Indicative BSE Intraday)", type: "FNO", exchange: "BFO" },
    RELIANCE:   { lot: 1, step: 1, min: 1, leverage: "5X MIS (NSE Equity Intraday)", type: "EQUITY", exchange: "NSE" },
    HDFCBANK:   { lot: 1, step: 1, min: 1, leverage: "5X MIS (NSE Equity Intraday)", type: "EQUITY", exchange: "NSE" },
    TATAMOTORS: { lot: 1, step: 1, min: 1, leverage: "5X MIS (NSE Equity Intraday)", type: "EQUITY", exchange: "NSE" },
    TCS:        { lot: 1, step: 1, min: 1, leverage: "5X MIS (BSE Equity Intraday)", type: "EQUITY", exchange: "BSE" },
    INFY:       { lot: 1, step: 1, min: 1, leverage: "5X MIS (BSE Equity Intraday)", type: "EQUITY", exchange: "BSE" },
    BTCUSDT:    { lot: 0.001, step: 0.001, min: 0.0001, leverage: "Up to 50X Perp (CoinDCX)", type: "CRYPTO_FUTURES", exchange: "COINDCX" },
    ETHUSDT:    { lot: 0.01, step: 0.01, min: 0.001, leverage: "Up to 50X Perp (CoinDCX)", type: "CRYPTO_FUTURES", exchange: "COINDCX" },
    PAXGUSDT:   { lot: 0.01, step: 0.01, min: 0.001, leverage: "Tokenized Gold Perp (CoinDCX)", type: "CRYPTO_FUTURES", exchange: "COINDCX" }
};

const MASTER_ASSETS = [
    { id: "nifty50", name: "NIFTY 50", category: "indian", symbol: "NSE:NIFTY", orderKey: "NIFTY", token: "26000", icon: "₹", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "INDEX", eventTime: 0 }, keywords: ["nifty", "nse", "indian market"] },
    { id: "banknifty", name: "BANK NIFTY", category: "indian", symbol: "NSE:BANKNIFTY", orderKey: "BANKNIFTY", token: "26009", icon: "₹", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "INDEX", eventTime: 0 }, keywords: ["bank nifty", "hdfc bank", "icici bank", "sbi", "banking"] },
    { id: "sensex", name: "SENSEX", category: "indian", symbol: "BSE:SENSEX", orderKey: "SENSEX", token: "1", icon: "₹", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "INDEX", eventTime: 0 }, keywords: ["sensex", "bse", "indian market"] },
    { id: "bankex", name: "BSE BANKEX", category: "indian", symbol: "BSE:BANKEX", orderKey: "BANKEX", token: "12", icon: "🏛️", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "INDEX", eventTime: 0 }, keywords: ["bankex", "bse banking", "bse"] },
    { id: "reliance", name: "RELIANCE", category: "indian", symbol: "NSE:RELIANCE", orderKey: "RELIANCE", token: "2885", icon: "₹", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "EQUITY", eventTime: 0 }, keywords: ["reliance", "jio", "oil", "ril"] },
    { id: "hdfcbank", name: "HDFC BANK", category: "indian", symbol: "NSE:HDFCBANK", orderKey: "HDFCBANK", token: "1333", icon: "₹", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "EQUITY", eventTime: 0 }, keywords: ["hdfc", "banking"] },
    { id: "tatamotors", name: "TATA MOTORS", category: "indian", symbol: "NSE:TATAMOTORS", orderKey: "TATAMOTORS", token: "3456", icon: "₹", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "EQUITY", eventTime: 0 }, keywords: ["tata", "ev", "auto", "tatamotors"] },
    { id: "tcs_bse", name: "TCS (BSE)", category: "indian", symbol: "BSE:TCS", orderKey: "TCS", token: "532540", icon: "💻", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "EQUITY", eventTime: 0 }, keywords: ["tcs", "tata consultancy", "bse it"] },
    { id: "infy_bse", name: "INFOSYS (BSE)", category: "indian", symbol: "BSE:INFY", orderKey: "INFY", token: "500209", icon: "📈", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "EQUITY", eventTime: 0 }, keywords: ["infy", "infosys", "bse it"] },
    { id: "btcusdt", name: "BTC / USDT", category: "crypto", symbol: "BINANCE:BTCUSDT", orderKey: "BTCUSDT", token: "btcusdt", icon: "₿", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "SPOT_REF", eventTime: 0 }, keywords: ["btc", "bitcoin", "crypto"] },
    { id: "ethusdt", name: "ETH / USDT", category: "crypto", symbol: "BINANCE:ETHUSDT", orderKey: "ETHUSDT", token: "ethusdt", icon: "Ξ", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "SPOT_REF", eventTime: 0 }, keywords: ["eth", "ethereum", "crypto"] },
    { id: "goldmcx", name: "PAXG / USDT (Tokenized Gold)", category: "commodity", symbol: "BINANCE:PAXGUSDT", orderKey: "PAXGUSDT", token: "paxgusdt", icon: "🟡", price: "Offline", priceNum: 0, high: 0, low: 0, highFormatted: "-", lowFormatted: "-", change: "0.00%", trend: "UNKNOWN", signalType: "Connecting Feed...", probabilityScore: 0, realTbq: 0, realTsq: 0, blockOrderDetected: false, dataEnvelope: { quality: "OFFLINE", source: "NONE", marketType: "SPOT_REF", eventTime: 0 }, keywords: ["gold", "paxg", "bullion", "tokenized gold"] }
];

currentSelectedAsset = MASTER_ASSETS[0];
const GEOPOLITICAL_KEYWORDS = ["trump", "tariff", "war", "geopolitic", "middle east", "iran", "israel", "china", "fed rate", "federal reserve", "crude oil", "rbi", "inflation", "trade war", "sanction", "strike", "rbi rate", "fii", "dii", "institutional flow", "sebi", "earnings", "merger", "acquisition"];
let activeIpoFilter = "all";

function sanitizeCleanText(str) {
    if (str === null || str === undefined) return "";
    const temp = document.createElement("div");
    temp.textContent = str.toString();
    return temp.innerHTML;
}

function normalizeFastApiTargetResponse(target, response) {
    if (target === "health" && response.ok && response.data) {
        response.data = {
            ...response.data,
            ok: response.data.status === "ok",
            liveTradingEnabled: response.data.live_trading_enabled === true
        };
    }
    return response;
}

async function secureApiCall(target, options = {}) {
    const fastApiPath = FASTAPI_TARGET_ROUTES[target];
    if (fastApiPath) {
        const response = await fastApiRequest(fastApiPath, {
            method: options.method || "GET",
            body: options.jsonBody,
            headers: options.headers,
            timeoutMs: options.timeoutMs,
            forceTokenRefresh: options.forceTokenRefresh === true
        });
        const fallbackStatuses = target === "get_master_user"
            ? [0, 401, 404, 500, 501, 502, 503, 504]
            : [0, 404, 500, 501, 502, 503, 504];
        if (
            response.ok ||
            !FASTAPI_AUTH_FALLBACK_TARGETS.has(target) ||
            !fallbackStatuses.includes(response.status)
        ) {
            return normalizeFastApiTargetResponse(target, response);
        }
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), options.timeoutMs || 15000);

    try {
        const headers = { ...(options.headers || {}) };
        if (auth.currentUser) {
            const idToken = await auth.currentUser.getIdToken(options.forceTokenRefresh === true);
            headers["Authorization"] = `Bearer ${idToken}`;
        }
        if (SESSION_VAULT.pinSessionToken) {
            headers["X-Pin-Token"] = SESSION_VAULT.pinSessionToken;
        }
        if (options.jsonBody !== undefined) {
            headers["Content-Type"] = "application/json";
        }

        const proxyQuery = new URLSearchParams({ target });
        if (options.query) {
            new URLSearchParams(options.query).forEach((value, key) => proxyQuery.append(key, value));
        }
        const res = await fetch(`${FASTAPI_BASE_URL}/legacy?${proxyQuery.toString()}`, {
            method: options.method || "GET",
            headers,
            body: options.jsonBody !== undefined ? JSON.stringify(options.jsonBody) : options.body,
            signal: controller.signal
        });
        const data = await res.json().catch(() => ({}));
        return { ok: res.ok, status: res.status, data };
    } catch (e) {
        return { ok: false, status: 0, data: { error: e.name === "AbortError" ? "Request timeout" : e.message } };
    } finally {
        clearTimeout(timeoutId);
    }
}

function loadLocalTerminalPreferences() {
    try {
        const saved = JSON.parse(localStorage.getItem(LOCAL_TERMINAL_SETTINGS_KEY) || "{}");
        terminalPreferences = { ...terminalPreferences, ...(saved && typeof saved === "object" ? saved : {}) };
    } catch (error) {
        console.warn("[Preferences] Ignoring invalid local terminal settings:", error.message);
    }
    applyTerminalTheme(terminalPreferences.theme);
    if (terminalPreferences.accent) {
        document.documentElement.style.setProperty('--groww-green', terminalPreferences.accent === 'mint' ? '#00d09c' : terminalPreferences.accent === 'emerald' ? '#059669' : '#3b82f6');
        document.documentElement.style.setProperty('--groww-blue', terminalPreferences.accent === 'blue' ? '#3b82f6' : terminalPreferences.accent === 'emerald' ? '#059669' : '#00d09c');
    }
    if (terminalPreferences.news_lang) SESSION_VAULT.newsLang = terminalPreferences.news_lang;
    if (typeof newsVoiceDigestEnabled !== "undefined") {
        newsVoiceDigestEnabled = terminalPreferences.news_voice_digest_enabled !== false;
    }
    updateAccentButtons();
}

function loadLocalRiskConfig() {
    try {
        const saved = JSON.parse(localStorage.getItem("ARAF_RISK_CONFIG") || "{}");
        const savedCapital = Number(saved.capital_inr || 100000);
        const savedLossAmount = saved.max_daily_loss_amount
            ?? (Number.isFinite(Number(saved.max_daily_loss_pct))
                ? savedCapital * Number(saved.max_daily_loss_pct) / 100
                : undefined);
        const fields = {
            riskCapitalInput: saved.capital_inr,
            riskMaxDailyLossAmount: savedLossAmount,
            riskPerTradePctInput: saved.max_per_trade_risk_pct,
            riskMaxTradesSelect: Math.min(1000, Math.max(1, Number(saved.max_trades_per_day || 3)))
        };
        Object.entries(fields).forEach(([id, value]) => {
            const input = document.getElementById(id);
            if (input && value !== undefined && Number.isFinite(Number(value))) input.value = String(value);
        });
    } catch (error) {
        console.warn("[Risk settings] Ignoring invalid local risk configuration:", error.message);
    }
}

function applyTerminalTheme(theme, updateButton = true) {
    terminalPreferences.theme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = terminalPreferences.theme;
    const metaTheme = document.querySelector('meta[name="theme-color"]');
    if (metaTheme) metaTheme.content = terminalPreferences.theme === "dark" ? "#0b1220" : "#ffffff";
    const button = document.getElementById("themeToggleButton");
    if (button && updateButton) {
        button.innerHTML = terminalPreferences.theme === "dark" ? '<i class="fa-solid fa-sun text-sm"></i>' : '<i class="fa-solid fa-moon text-sm"></i>';
        button.setAttribute("aria-pressed", String(terminalPreferences.theme === "dark"));
    }
    updateThemeSelectorButtons();
}

function updateThemeSelectorButtons() {
    const lightBtn = document.getElementById("themeBtnLight");
    const darkBtn = document.getElementById("themeBtnDark");
    if (lightBtn && darkBtn) {
        if (terminalPreferences.theme === "dark") {
            lightBtn.className = "flex-1 px-3 py-2 rounded-xl bg-white border-2 border-slate-200 text-slate-700 font-bold text-[10px] transition hover:border-[#00d09c]";
            darkBtn.className = "flex-1 px-3 py-2 rounded-xl bg-[#00d09c] border-2 border-[#00d09c] text-white font-bold text-[10px] transition";
        } else {
            lightBtn.className = "flex-1 px-3 py-2 rounded-xl bg-[#00d09c] border-2 border-[#00d09c] text-white font-bold text-[10px] transition";
            darkBtn.className = "flex-1 px-3 py-2 rounded-xl bg-white border-2 border-slate-200 text-slate-700 font-bold text-[10px] transition hover:border-[#00d09c]";
        }
    }
}

function setTheme(theme) {
    applyTerminalTheme(theme);
    void saveTerminalPreferences();
}

function setAccent(accent) {
    terminalPreferences.accent = accent;
    document.documentElement.style.setProperty('--groww-green', accent === 'mint' ? '#00d09c' : accent === 'emerald' ? '#059669' : '#3b82f6');
    document.documentElement.style.setProperty('--groww-blue', accent === 'blue' ? '#3b82f6' : accent === 'emerald' ? '#059669' : '#00d09c');
    void saveTerminalPreferences();
    updateAccentButtons();
}

function updateAccentButtons() {
    const accents = ['mint', 'emerald', 'blue'];
    accents.forEach(a => {
        const btn = document.getElementById(`accentBtn${a.charAt(0).toUpperCase() + a.slice(1)}`);
        if (btn) {
            if (terminalPreferences.accent === a) {
                btn.className = "flex-1 px-3 py-2 rounded-xl bg-[#00d09c] border-2 border-[#00d09c] text-white font-bold text-[10px] transition";
            } else {
                btn.className = "flex-1 px-3 py-2 rounded-xl bg-white border-2 border-slate-200 text-slate-700 font-bold text-[10px] transition hover:border-[#00d09c]";
            }
        }
    });
}

async function fastApiRequest(path, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), options.timeoutMs || 12000);
    try {
        const headers = { ...(options.headers || {}) };
        if (auth.currentUser) {
            headers.Authorization = `Bearer ${await auth.currentUser.getIdToken(options.forceTokenRefresh === true)}`;
        }
        if (SESSION_VAULT.pinSessionToken) {
            headers["X-Pin-Token"] = SESSION_VAULT.pinSessionToken;
        }
        if (options.body !== undefined) headers["Content-Type"] = "application/json";
        const response = await fetch(`${FASTAPI_BASE_URL}${path}`, {
            method: options.method || "GET",
            headers,
            body: options.body === undefined ? undefined : JSON.stringify(options.body),
            signal: controller.signal,
            cache: "no-store"
        });
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok, status: response.status, data };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            data: { detail: error.name === "AbortError" ? "FastAPI request timed out" : error.message }
        };
    } finally {
        clearTimeout(timeoutId);
    }
}

async function syncTerminalPreferences() {
    if (!auth.currentUser) return;
    const response = await fastApiRequest("/preferences");
    if (!response.ok || !response.data?.preferences) {
        const status = document.getElementById("themeSyncStatus");
        if (status) status.textContent = "Local preference active; server sync unavailable.";
        return;
    }
    terminalPreferences = { ...terminalPreferences, ...response.data.preferences };
    applyTerminalTheme(terminalPreferences.theme);
    if (terminalPreferences.accent) {
        document.documentElement.style.setProperty('--groww-green', terminalPreferences.accent === 'mint' ? '#00d09c' : terminalPreferences.accent === 'emerald' ? '#059669' : '#3b82f6');
        document.documentElement.style.setProperty('--groww-blue', terminalPreferences.accent === 'blue' ? '#3b82f6' : terminalPreferences.accent === 'emerald' ? '#059669' : '#00d09c');
    }
    SESSION_VAULT.newsLang = terminalPreferences.news_lang || "en-US";
    newsVoiceDigestEnabled = terminalPreferences.news_voice_digest_enabled !== false;
    localStorage.setItem(LOCAL_TERMINAL_SETTINGS_KEY, JSON.stringify(terminalPreferences));
    const status = document.getElementById("themeSyncStatus");
    if (status) status.textContent = "Settings synced.";
    updateAccentButtons();
}

async function saveTerminalPreferences() {
    localStorage.setItem(LOCAL_TERMINAL_SETTINGS_KEY, JSON.stringify(terminalPreferences));
    if (!auth.currentUser) return;
    const response = await fastApiRequest("/preferences", {
        method: "PUT",
        body: terminalPreferences
    });
    const status = document.getElementById("themeSyncStatus");
    if (status) status.textContent = response.ok ? "Settings synced." : "Saved locally; server sync failed.";
}

function toggleTerminalTheme() {
    applyTerminalTheme(terminalPreferences.theme === "dark" ? "light" : "dark");
    void saveTerminalPreferences();
}

async function testFastApiQuote(broker) {
    const output = document.getElementById("apiFeedTestResult");
    if (!output) return;
    output.classList.remove("hidden");
    output.textContent = "Checking authenticated read-only quote endpoint...";

    try {
        if (!auth.currentUser) throw new Error("Sign in first; Firebase authentication is required.");
        const token = await auth.currentUser.getIdToken();
        const sessionResponse = await fetch(`${FASTAPI_BASE_URL}/auth/session`, {
            method: "POST",
            headers: { "Authorization": `Bearer ${token}` },
            cache: "no-store"
        });
        const sessionData = await sessionResponse.json().catch(() => ({}));
        if (!sessionResponse.ok) {
            throw new Error(`FastAPI session verification failed (${sessionResponse.status}).`);
        }

        let endpoint;
        let body;
        if (broker === "SHOONYA") {
            const symbol = document.getElementById("apiShoonyaToken")?.value.trim();
            const exchange = document.getElementById("apiShoonyaExchange")?.value;
            if (!symbol) throw new Error("Enter the Shoonya instrument token.");
            endpoint = "/brokers/test/shoonya/quote";
            body = { symbol, exchange };
        } else if (broker === "COINDCX") {
            const symbol = document.getElementById("apiCoinDcxPair")?.value.trim();
            if (!symbol) throw new Error("Enter a CoinDCX futures pair, such as BTCUSDT.");
            endpoint = "/brokers/test/coindcx/quote";
            body = { symbol };
        } else {
            throw new Error("Unknown broker quote test.");
        }

        const response = await fetch(`${FASTAPI_BASE_URL}${endpoint}`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${token}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify(body),
            cache: "no-store"
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
            throw new Error(`API ${response.status}: ${detail}`);
        }
        data.session_persistence = sessionData.persistence;
        output.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
        output.textContent = `Feed test failed: ${error.message || "Unknown error"}`;
    }
}

const ArafCloudMaster = {
    async syncAllUserData(_unused, fullData) {
        if (!auth.currentUser) return false;
        const nowIso = new Date().toISOString();
        const safePayload = {
            preferences: fullData?.preferences || {},
            updatedAt: nowIso
        };
        try {
            const fsDocRef = db.collection("users").doc(auth.currentUser.uid);
            const docSnap = await fsDocRef.get();
            const fsPayload = {
                ...safePayload,
                displayName: auth.currentUser.displayName || "",
                locale: SESSION_VAULT.voiceLang || "en-US"
            };
            if (!docSnap.exists) {
                fsPayload.createdAt = nowIso;
            }

            const [kvRes, fsRes] = await Promise.allSettled([
                secureApiCall("save_master_user", { method: "POST", jsonBody: { fullData: safePayload } }),
                fsDocRef.set(fsPayload, { merge: true })
            ]);
            const kvOk = kvRes.status === "fulfilled" && kvRes.value.ok;
            const fsOk = fsRes.status === "fulfilled";
            return kvOk || fsOk;
        } catch (e) {
            console.warn("[Cloud Sync Error]:", e);
            return false;
        }
    },
    async loadUserFromAnyDevice(forceTokenRefresh = false) {
        if (!auth.currentUser) return { status: "NO_AUTH", fullData: null };
        try {
            const res = await secureApiCall("get_master_user", { method: "GET", forceTokenRefresh });
            if (res.ok && res.data?.success) {
                SESSION_VAULT.hasServerPin = res.data.hasPin === true;
                return { status: "OK", fullData: res.data.fullData };
            }
            if (res.status === 404) return { status: "NOT_FOUND", fullData: null };
            return { status: "ERROR", httpStatus: res.status, message: res.data?.error || res.data?.message || "Server unreachable", fullData: null };
        } catch (e) {
            return { status: "ERROR", httpStatus: 0, message: e.message || "Network error", fullData: null };
        }
    }
};

function applySettingsCredentialLock(type, isConnected) {
    const idsByType = {
        
        shoonya: ["settingShoonyaUid", "settingShoonyaVendor", "settingShoonyaPassword", "settingShoonyaTotp", "settingShoonyaApiKey", "settingShoonyaToken"],
        coindcx: ["settingCoinDcxKey", "settingCoinDcxSecret"]
    };
    (idsByType[type] || []).forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.disabled = !!isConnected;
        el.classList.toggle("araf-blur-lock", !!isConnected);
        if (isConnected) el.value = "••••••••••••";
    });
}

function checkSettingsRiskGuardVisibility() {
    const card = document.getElementById("settingsRiskGuardCard");
    if (!card) return;
    if (!window.__arafRiskVisibilityTimer) {
        window.__arafRiskVisibilityTimer = setInterval(checkSettingsRiskGuardVisibility, 60000);
    }
    const lockedDate = SESSION_VAULT.serverRiskState?.lockedConfigDate;
    const today = getIndiaTradingDate();
    if (lockedDate !== today) {
        card.classList.remove("hidden");
        return;
    }
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Kolkata", hour12: false, hour: "numeric", minute: "numeric"
    }).formatToParts(new Date());
    const hour = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const minute = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    const currentMinutes = hour * 60 + minute;
    card.classList.toggle("hidden", currentMinutes < 555);
}

function applyRiskStateToUi(risk) {
    if (!risk) return;
    const normalized = {
        ...risk,
        capitalInr: risk.capitalInr ?? risk.capital_inr,
        maxDailyLossAmount: risk.maxDailyLossAmount ?? risk.max_daily_loss_amount,
        maxPerTradeRiskPct: risk.maxPerTradeRiskPct ?? risk.max_per_trade_risk_pct,
        maxTradesPerDay: Math.min(1000, Number(risk.maxTradesPerDay ?? risk.max_trades_per_day ?? 3)),
        dailyPnlInr: risk.dailyPnlInr ?? risk.daily_pnl_inr,
        tradesCount: risk.tradesCount ?? risk.trades_count,
        isHardLocked: risk.isHardLocked ?? risk.is_hard_locked,
        lockedReason: risk.lockedReason ?? risk.locked_reason,
        riskLockExpiresAt: risk.riskLockExpiresAt ?? risk.risk_lock_expires_at,
        dailyLossLimitInr: risk.dailyLossLimitInr ?? risk.daily_loss_limit_inr
    };
    if (!Number.isFinite(Number(normalized.dailyLossLimitInr))) {
        normalized.dailyLossLimitInr = -Math.abs(Number(normalized.maxDailyLossAmount || 2500));
    }
    SESSION_VAULT.serverRiskState = normalized;
    serverRiskStateLoaded = true;
    riskPnlSyncFailed = false;
    const capEl = document.getElementById("riskCapitalInput");
    const lossAmountEl = document.getElementById("riskMaxDailyLossAmount");
    const tradeRiskEl = document.getElementById("riskPerTradePctInput");
    const maxTrEl = document.getElementById("riskMaxTradesSelect");
    const badgeEl = document.getElementById("riskLimitDisplayBadge");
    const lockStatusEl = document.getElementById("riskLockStatus");
    const riskInputs = [capEl, lossAmountEl, tradeRiskEl, maxTrEl];
    const lockExpiresAt = Date.parse(normalized.riskLockExpiresAt || "");
    const configLocked = (
        Number.isFinite(lockExpiresAt) && lockExpiresAt > Date.now()
    ) || normalized.isHardLocked === true;

    if (capEl && normalized.capitalInr) capEl.value = normalized.capitalInr;
    if (lossAmountEl && normalized.maxDailyLossAmount) lossAmountEl.value = normalized.maxDailyLossAmount;
    if (tradeRiskEl && normalized.maxPerTradeRiskPct) tradeRiskEl.value = normalized.maxPerTradeRiskPct;
    if (maxTrEl && normalized.maxTradesPerDay) maxTrEl.value = normalized.maxTradesPerDay;
    riskInputs.forEach(input => { if (input) input.disabled = configLocked; });
    const saveRiskButton = document.querySelector("#settingsRiskGuardCard button[onclick='saveUserRiskConfig()']");
    const syncCapitalButton = document.querySelector("#settingsRiskGuardCard button[onclick='syncBrokerCapitalToRiskInput()']");
    if (saveRiskButton) saveRiskButton.disabled = configLocked;
    if (syncCapitalButton) syncCapitalButton.disabled = configLocked;
    if (lockStatusEl) {
        lockStatusEl.textContent = configLocked
            ? `Server locked until ${new Date(lockExpiresAt).toLocaleString()}`
            : "Risk settings unlock when the 24-hour server lock expires.";
    }
    if (badgeEl) {
        const lim = Math.abs(normalized.dailyLossLimitInr || 2500);
        badgeEl.textContent = `Limit: -₹${lim.toLocaleString("en-IN")} (${normalized.tradesCount || 0}/${normalized.maxTradesPerDay || 3})`;
    }
    calculateSafeQtySuggestion();
    checkHardKillSwitchLock();
    checkSettingsRiskGuardVisibility();
    if (serverAutoTradeStateLoaded) renderAutoTradeUi();
}

function checkHardKillSwitchLock() {
    if (auth.currentUser && (!serverRiskStateLoaded || riskPnlSyncFailed)) {
        setRiskControlsDisabled(true);
        const hud = document.getElementById("hudUserStatusText");
        if (hud) hud.textContent = "Server risk status unavailable — entries blocked";
        return true;
    }
    if (riskPnlSyncFailed) {
        const hud = document.getElementById("hudUserStatusText");
        if (hud) hud.textContent = "Risk P&L sync unavailable — entries blocked";
        return true;
    }
    if (SESSION_VAULT.serverRiskState?.isHardLocked === true) {
        applyHardLockUi(SESSION_VAULT.serverRiskState.dailyPnlInr || getActiveDailyLossLimitInr());
        return true;
    }
    return false;
}

function setRiskControlsDisabled(disabled) {
    [
        "riskCapitalInput",
        "riskMaxDailyLossAmount",
        "riskPerTradePctInput",
        "riskMaxTradesSelect"
    ].forEach(id => {
        const input = document.getElementById(id);
        if (input) input.disabled = disabled;
    });
    const saveRiskButton = document.querySelector("#settingsRiskGuardCard button[onclick='saveUserRiskConfig()']");
    const syncCapitalButton = document.querySelector("#settingsRiskGuardCard button[onclick='syncBrokerCapitalToRiskInput()']");
    if (saveRiskButton) saveRiskButton.disabled = disabled;
    if (syncCapitalButton) syncCapitalButton.disabled = disabled;
}

function applyHardLockUi(lossAmount) {
    const hud = document.getElementById("hudUserStatusText");
    if (hud) {
        hud.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-[#eb5b3c] animate-ping mr-1"></span> 🛑 SERVER RISK HARD-LOCK (Loss: ₹${Math.abs(lossAmount).toFixed(2)})`;
    }
}

function syncDailyPnlToServer(dailyPnlInr, force = false) {
    if (!auth.currentUser || !Number.isFinite(dailyPnlInr) || isPnlBreachSyncInFlight) return;
    const now = Date.now();
    if (!force && now - lastRiskPnlSyncAt < 1000) return;
    lastRiskPnlSyncAt = now;
    isPnlBreachSyncInFlight = true;
    fastApiRequest("/risk/pnl", {
        method: "POST",
        body: { daily_pnl_inr: dailyPnlInr }
    }).then(res => {
        if (!res.ok || !res.data?.risk) {
            riskPnlSyncFailed = true;
            console.error("Server risk P&L synchronization failed", res.data?.detail || res.status);
            checkHardKillSwitchLock();
            return;
        }
        riskPnlSyncFailed = false;
        applyRiskStateToUi(res.data.risk);
    }).catch(error => {
        riskPnlSyncFailed = true;
        console.error("Server risk P&L synchronization failed", error);
        checkHardKillSwitchLock();
    }).finally(() => {
        isPnlBreachSyncInFlight = false;
    });
}

async function syncServerRiskState() {
    if (!auth.currentUser) return false;
    try {
        const res = await fastApiRequest("/risk/state");
        if (!res.ok || !res.data?.risk) {
            throw new Error(res.data?.detail || `Risk state request failed (${res.status})`);
        }
        applyRiskStateToUi(res.data.risk);
        return true;
    } catch (error) {
        riskPnlSyncFailed = true;
        console.error("Authoritative server risk state could not be loaded", error);
        checkHardKillSwitchLock();
        return false;
    }
}

async function syncBrokerCapitalToRiskInput() {
    const shoonyaTxt = document.getElementById("hudShoonyaBalance")?.textContent || "";
    const inrBal = parseFloat(shoonyaTxt.replace(/[^0-9.]/g, "")) || 0;
    const total = Math.round(inrBal);
    if (total < 5000) {
        alert("Could not read sufficient live broker balance (min ₹5,000). Enter Capital manually.");
        return;
    }
    const capEl = document.getElementById("riskCapitalInput");
    if (capEl) capEl.value = total;
    calculateSafeQtySuggestion();
}

async function saveUserRiskConfig() {
    const capitalInr = Number(document.getElementById("riskCapitalInput")?.value || 100000);
    const maxDailyLossAmount = Number(document.getElementById("riskMaxDailyLossAmount")?.value || 2500);
    const maxTradesPerDay = Number(document.getElementById("riskMaxTradesSelect")?.value || 3);
    const maxPerTradeRiskPct = Number(document.getElementById("riskPerTradePctInput")?.value || 1);
    if (![capitalInr, maxDailyLossAmount, maxTradesPerDay, maxPerTradeRiskPct].every(Number.isFinite)
        || maxDailyLossAmount < 1 || maxDailyLossAmount > 100000000
        || !Number.isInteger(maxTradesPerDay) || maxTradesPerDay < 1 || maxTradesPerDay > 1000) {
        alert("Enter a daily loss amount from ₹1 to ₹10 crore and max daily trades between 1 and 1,000.");
        return;
    }

    const res = await fastApiRequest("/risk/configure", {
        method: "POST",
        body: {
            capital_inr: capitalInr,
            max_daily_loss_amount: maxDailyLossAmount,
            max_trades_per_day: maxTradesPerDay,
            max_per_trade_risk_pct: maxPerTradeRiskPct
        }
    });
    if (res.ok && res.data?.success) {
        localStorage.setItem("ARAF_RISK_CONFIG", JSON.stringify({
            capital_inr: capitalInr,
            max_daily_loss_amount: maxDailyLossAmount,
            max_trades_per_day: maxTradesPerDay,
            max_per_trade_risk_pct: maxPerTradeRiskPct
        }));
        applyRiskStateToUi(res.data.risk);
        alert(`Day Risk Locked on Server!\nCapital: ₹${Number(res.data.risk.capital_inr).toLocaleString("en-IN")}\nDaily Loss Limit: -₹${Math.abs(Number(res.data.risk.daily_loss_limit_inr || 0)).toLocaleString("en-IN")}\nMax Trades: ${res.data.risk.max_trades_per_day}`);
    } else {
        const detail = res.data?.detail;
        alert(typeof detail === "string" ? detail : (detail?.message || "Failed to update risk config."));
    }
}

function calculateSafeQtySuggestion() {
    const symbol = document.getElementById("tradeOrderSymbol")?.value;
    const canonical = resolveCanonicalInstrument(symbol);
    const asset = MASTER_ASSETS.find(a => a.orderKey === symbol) || DYNAMIC_SCAN_CACHE.find(a => a.orderKey === symbol);
    const ltp = asset?.priceNum || asset?.price || 0;
    const hintEl = document.getElementById("riskSafeQtyHint");
    if (!hintEl) return;

    const slVal = Number(document.getElementById("tradeSlPriceInput")?.value);
    const capInr = Number(document.getElementById("riskCapitalInput")?.value) || SESSION_VAULT.serverRiskState?.capitalInr || 100000;
    const configuredRiskPct = Number(document.getElementById("riskPerTradePctInput")?.value)
        || SESSION_VAULT.serverRiskState?.maxPerTradeRiskPct
        || 1;
    const riskBudgetInr = capInr * configuredRiskPct / 100;

    if (!canonical || ltp <= 0 || !Number.isFinite(slVal) || slVal <= 0 || Math.abs(ltp - slVal) === 0) {
        hintEl.textContent = "Safe Qty: Waiting for LTP/SL";
        return;
    }

    let riskPerUnitInr = Math.abs(ltp - slVal);
    if (canonical.broker === "COINDCX") {
        const fx = FX_RATE_STATE.usdToInr;
        if (!fx) { hintEl.textContent = "Safe Qty: FX Offline"; return; }
        riskPerUnitInr = riskPerUnitInr * fx;
    }

    const rawQty = riskBudgetInr / riskPerUnitInr;
    if (canonical.broker === "SHOONYA") {
        const lots = Math.max(1, Math.floor(rawQty / canonical.lotSize));
        hintEl.textContent = `Safe Qty: ${lots * canonical.lotSize} (Budget ₹${Math.round(riskBudgetInr)})`;
    } else {
        const maxCap = canonical.maxQty || 10;
        const finalCalculated = Math.min(maxCap, Math.max(canonical.lotSize, Number(rawQty.toFixed(4))));
        hintEl.textContent = `Safe Qty: ${finalCalculated} (Budget ₹${Math.round(riskBudgetInr)})`;
    }
}

async function resetDailyKillSwitchManual() {
    await syncServerRiskState();
    if (checkHardKillSwitchLock()) {
        alert("Server Risk Hard-Lock Active! Daily loss limit reached. New entries are blocked on the server until tomorrow.");
        return;
    }
    const name = SESSION_VAULT.activeUser?.fullName || "ARAF TRADER";
    document.getElementById("hudUserStatusText").innerHTML =
        `<span class="w-1.5 h-1.5 rounded-full bg-[#00d09c] animate-pulse mr-1"></span> ${sanitizeCleanText(name)} • Risk Guard OK`;
    syncRealBrokerBalances();
    initSyncPositionsToMemory();
}

function checkIntradaySquareOffReminder() {
    const now = new Date();
    if (isNseHoliday(now)) return;
    const istParts = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Kolkata", hour12: false, hour: "numeric", minute: "numeric"
    }).formatToParts(now);

    const hour = parseInt(istParts.find(p => p.type === "hour")?.value || "0", 10);
    const minute = parseInt(istParts.find(p => p.type === "minute")?.value || "0", 10);

    if (hour === 15 && minute >= 10 && minute <= 14) {
        const todayStr = getIndiaTradingDate();
        const alertKey = `ARAF_310_ALERT_${todayStr}`;
        if (!localStorage.getItem(alertKey)) {
            localStorage.setItem(alertKey, "triggered");
            playOpportunitySonarBeep("SELL");
            document.getElementById("intradaySquareOffModal")?.classList.remove("hidden");
            const voiceSqOff = (SESSION_VAULT.voiceLang === "bn-IN")
                ? "Intraday alert! 3:10 PM - close all positions before broker auto square-off penalty."
                : "Intraday Alert! It is 3:15 PM IST. MIS positions will be auto square-off by the broker soon.";
            explainTextFriendly(voiceSqOff, "3:15 PM Square-Off");
        }
    }
}

function checkProAccess() {
    if (!SESSION_VAULT.activeUser) return false;
    const st = SESSION_VAULT.activeUser.kycStatus;
    if (st !== "APPROVED" && st !== "VERIFIED_COMPLIANT") {
        showPendingAdminHoldScreen(SESSION_VAULT.activeUser);
        return false;
    }
    if (isSubscriptionActive(SESSION_VAULT.activeUser)) return true;
    showSubscriptionPaywall();
    return false;
}

function isSubscriptionActive(profile) {
    if (!profile) return false;
    if (profile.isVipFree === true) return true;
    const expiresAt = Date.parse(profile.subscriptionExpiresAt || "");
    return Number.isFinite(expiresAt) && expiresAt > Date.now();
}

function updateSubscriptionStatusUi() {
    const badge = document.getElementById("settingsSubscriptionBadge");
    const profile = SESSION_VAULT.activeUser;
    if (!badge || !profile) return;
    if (isSubscriptionActive(profile)) {
        const label = profile.isVipFree
            ? "Active ✅ (Lifetime VIP)"
            : `Active ✅ (Expires: ${new Intl.DateTimeFormat("en-GB", {
                timeZone: "Asia/Kolkata",
                day: "2-digit",
                month: "2-digit",
                year: "numeric"
            }).format(new Date(profile.subscriptionExpiresAt)).replaceAll("/", "-")})`;
        badge.textContent = label;
        badge.className = "inline-flex rounded-full border border-emerald-400/60 bg-emerald-500/15 px-2.5 py-1 text-[9px] font-mono font-black text-emerald-700 shadow-[0_0_12px_rgba(16,185,129,0.35)]";
        return;
    }
    badge.textContent = "Inactive • Subscription required";
    badge.className = "inline-flex rounded-full border border-rose-300 bg-rose-50 px-2.5 py-1 text-[9px] font-mono font-bold text-rose-700";
}

function showSubscriptionPaywall() {
    const profile = SESSION_VAULT.activeUser;
    const priceEl = document.getElementById("displayDynamicSubPrice");
    if (priceEl) {
        priceEl.textContent = `₹${BUSINESS_PAYMENT_CONFIG.monthlyPrice.toLocaleString("en-IN")}`;
    }
    const uidDisplay = document.getElementById("displayUserTraderUid");
    if (uidDisplay) uidDisplay.textContent = profile?.traderUid || "PENDING";
    document.getElementById("subscriptionLockModal")?.classList.remove("hidden");
    document.getElementById("authKycModal")?.classList.add("hidden");
    document.getElementById("quickUnlockModal")?.classList.add("hidden");
    document.getElementById("setupPinModal")?.classList.add("hidden");
    document.getElementById("subscriptionPayButton")?.focus({ preventScroll: true });
    void syncGlobalSubscriptionPrice();
    if (!subscriptionStatusPollTimer && auth.currentUser) {
        subscriptionStatusPollTimer = window.setInterval(() => {
            if (!document.hidden) void refreshSubscriptionStatus();
        }, 15000);
    }
}

async function refreshSubscriptionStatus() {
    if (!auth.currentUser) return false;
    const result = await ArafCloudMaster.loadUserFromAnyDevice();
    if (result.status !== "OK" || !result.fullData?.profile) {
        const status = document.getElementById("subscriptionPaymentStatus");
        if (status) status.textContent = "Could not check subscription status. Please retry.";
        return false;
    }
    const profile = result.fullData.profile;
    SESSION_VAULT.savedCloudUser = result.fullData;
    SESSION_VAULT.activeUser = profile;
    updateSubscriptionStatusUi();
    
    // Check for account deletion status first
    if (profile.deletion_status === "DELETION_PENDING") {
        if (subscriptionStatusPollTimer) clearInterval(subscriptionStatusPollTimer);
        subscriptionStatusPollTimer = null;
        showAccountDeletionPendingModal();
        return false;
    }
    
    if (profile.isActive === false) {
        if (subscriptionStatusPollTimer) clearInterval(subscriptionStatusPollTimer);
        subscriptionStatusPollTimer = null;
        showPendingAdminHoldScreen(profile);
        return false;
    }
    if (!isSubscriptionActive(profile)) return false;

    if (subscriptionStatusPollTimer) clearInterval(subscriptionStatusPollTimer);
    subscriptionStatusPollTimer = null;
    document.getElementById("subscriptionLockModal")?.classList.add("hidden");
    const status = document.getElementById("subscriptionPaymentStatus");
    if (status) status.textContent = "Subscription activated. Access is now available.";
    if (profile.kycStatus !== "APPROVED" && profile.kycStatus !== "VERIFIED_COMPLIANT") {
        showPendingAdminHoldScreen(profile);
    } else if (SESSION_VAULT.hasServerPin) {
        const savedPinToken = sessionStorage.getItem("ARAF_PIN_SESSION");
        if (savedPinToken) {
            SESSION_VAULT.pinSessionToken = savedPinToken;
            void unlockTerminalSuccess();
        } else {
            document.getElementById("unlockWelcomeTitle").textContent =
                `Welcome, ${profile.fullName || "Trader"}!`;
            document.getElementById("quickUnlockModal")?.classList.remove("hidden");
        }
    } else {
        document.getElementById("setupPinModal")?.classList.remove("hidden");
    }
    return true;
}

function updateVipTrialBadgeUi() {
    updateSubscriptionStatusUi();
    const badge = document.getElementById("hudVipTrialBadge");
    if (!badge || !SESSION_VAULT.activeUser) return;

    if (SESSION_VAULT.activeUser.isVipFree) {
        badge.textContent = "👑 LIFETIME VIP";
        badge.className = "text-[8px] bg-amber-500 text-white font-mono font-black px-1.5 py-0.2 rounded shadow-sm";
        return;
    }
    const exp = new Date(SESSION_VAULT.activeUser.subscriptionExpiresAt || 0);
    const diffDays = Math.ceil((exp - new Date()) / (1000 * 60 * 60 * 24));
    if (diffDays > 0) {
        badge.textContent = `👑 VIP: ${diffDays}D LEFT`;
        badge.className = diffDays <= 3
            ? "text-[8px] bg-[#eb5b3c]/20 text-[#eb5b3c] border border-[#eb5b3c]/40 font-mono font-bold px-1.5 py-0.2 rounded animate-pulse"
            : "text-[8px] gold-badge px-1.5 py-0.2 rounded font-mono font-bold";
    } else {
        badge.textContent = "🔒 TRIAL ENDED";
        badge.className = "text-[8px] bg-[#eb5b3c] text-white font-mono font-black px-1.5 py-0.2 rounded";
    }
}

function getSelectedSubscriptionMonths() {
    const input = document.getElementById("subscriptionMonths");
    const months = Number.parseInt(input?.value || "1", 10);
    if (!Number.isInteger(months) || months < 1 || months > 12) {
        if (input) input.value = "1";
        return 1;
    }
    return months;
}

function updateSubscriptionMonthTotal() {
    const months = getSelectedSubscriptionMonths();
    const total = BUSINESS_PAYMENT_CONFIG.monthlyPrice * months;
    const totalEl = document.getElementById("subscriptionSelectedTotal");
    if (totalEl) {
        totalEl.textContent = `₹${total.toLocaleString("en-IN")}`;
    }
    const priceEl = document.getElementById("displayDynamicSubPrice");
    if (priceEl) {
        priceEl.textContent = `₹${BUSINESS_PAYMENT_CONFIG.monthlyPrice.toLocaleString("en-IN")}`;
    }
}

async function triggerDirectUpiPayment() {
    const status = document.getElementById("subscriptionPaymentStatus");
    const payButton = document.getElementById("subscriptionPayButton");
    const qrContainer = document.getElementById("upiQrContainer");
    if (payButton) payButton.disabled = true;
    const months = getSelectedSubscriptionMonths();
    if (status) status.textContent = "Preparing your secure UPI payment details…";
    try {
        const idToken = auth.currentUser
            ? await auth.currentUser.getIdToken()
            : null;
        const response = await fetch(
            `${FASTAPI_BASE_URL}/auth/subscription/upi-intent?months=${months}`,
            {
                method: "GET",
                headers: idToken ? { Authorization: `Bearer ${idToken}` } : {},
                cache: "no-store"
            }
        );
        const result = await response.json().catch(() => ({}));
        const resultWrapper = { ok: response.ok, status: response.status, data: result };
        if (!resultWrapper.ok || !resultWrapper.data?.success) {
            throw new Error(resultWrapper.data?.detail || resultWrapper.data?.message || "Payment details are unavailable.");
        }
        const { intent_uri: intentUri, qr_svg: qrSvg, amount_inr: amount } = resultWrapper.data;
        if (typeof intentUri !== "string" || !intentUri.startsWith("upi://pay?") || typeof qrSvg !== "string") {
            throw new Error("The server returned invalid UPI payment details.");
        }
        const qrImage = document.getElementById("upiIntentQr");
        const intentLink = document.getElementById("upiIntentLink");
        const copyButton = document.getElementById("copyUpiIntentButton");
        if (qrImage) qrImage.src = `data:image/svg+xml;base64,${btoa(qrSvg)}`;
        if (intentLink) {
            intentLink.href = intentUri;
            intentLink.classList.remove("hidden");
        }
        if (copyButton) {
            copyButton.dataset.intentUri = intentUri;
            copyButton.classList.remove("hidden");
        }
        if (qrContainer) qrContainer.classList.remove("hidden");
        if (status) status.textContent =
            `Pay ₹${Number(amount).toLocaleString("en-IN")} in your UPI app. Access unlocks after payment is verified by an admin.`;

        const isMobile = navigator.userAgentData?.mobile === true
            || /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
            || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
        if (isMobile) window.location.assign(intentUri);
    } catch (error) {
        console.error("[UPI Intent]:", error);
        if (status) status.textContent = error.message || "Could not prepare UPI payment. Please retry.";
    } finally {
        if (payButton) payButton.disabled = false;
    }
}

async function copyUpiIntentUri() {
    const intentUri = document.getElementById("copyUpiIntentButton")?.dataset.intentUri;
    const status = document.getElementById("subscriptionPaymentStatus");
    if (!intentUri) {
        if (status) status.textContent = "Prepare the UPI payment link first.";
        return;
    }
    try {
        await navigator.clipboard.writeText(intentUri);
        if (status) status.textContent = "UPI payment link copied.";
    } catch (error) {
        console.error("[UPI Link Copy]:", error);
        if (status) status.textContent = "Clipboard access failed. Use the visible UPI link instead.";
    }
}

async function submitSubscriptionPaymentProof() {
    const months = getSelectedSubscriptionMonths();
    const fileInput = document.getElementById("subscriptionProof");
    const file = fileInput?.files?.[0];
    const status = document.getElementById("subscriptionPaymentStatus");
    if (!file) {
        if (status) status.textContent = "Select a JPEG, PNG, or WebP payment proof screenshot.";
        return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        if (status) status.textContent = "Payment proof must be JPEG, PNG, or WebP.";
        return;
    }
    if (file.size > 5 * 1024 * 1024) {
        if (status) status.textContent = "Payment proof exceeds 5 MB.";
        return;
    }
    if (!auth.currentUser) {
        if (status) status.textContent = "Sign in before sending payment proof.";
        return;
    }

    if (status) status.textContent = "Sending payment proof to Telegram admin…";
    try {
        const idToken = await auth.currentUser.getIdToken();
        const form = new FormData();
        form.append("months", String(months));
        form.append("proof", file);
        const response = await fetch(
            `${FASTAPI_BASE_URL}/auth/subscription/payment-proof`,
            {
                method: "POST",
                headers: { Authorization: `Bearer ${idToken}` },
                body: form
            }
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) {
            throw new Error(data.detail || data.message || "Payment proof could not be sent.");
        }
        if (status) {
            status.textContent =
                `Payment proof sent for ${months} month${months === 1 ? "" : "s"}. Wait for Telegram admin approval.`;
        }
    } catch (error) {
        console.error("[Subscription Payment Proof]:", error);
        if (status) status.textContent = error.message || "Payment proof relay failed.";
    }
}

document.addEventListener("keydown", event => {
    const dialog = document.getElementById("subscriptionLockModal");
    if (!dialog || dialog.classList.contains("hidden")) return;
    if (event.key === "Escape") {
        event.preventDefault();
        return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialog.querySelectorAll(
        'button:not(:disabled):not(.hidden), a[href]:not(.hidden)'
    )).filter(element => element.getClientRects().length > 0);
    if (!focusable.length) {
        event.preventDefault();
        return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
    }
});

async function triggerBiometricFingerprintAuth() {
    if (window.PublicKeyCredential && PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
        try {
            const available = await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
            if (available) {
                const challenge = new Uint8Array(32);
                crypto.getRandomValues(challenge);
                const credential = await navigator.credentials.get({
                    publicKey: { challenge, timeout: 60000, userVerification: "required" }
                });
                if (credential) {
                    unlockTerminalSuccess();
                    return;
                }
            }
        } catch (e) {}
    }
    alert("Biometric sensor unavailable. Please unlock using your 6-digit PIN.");
    document.getElementById("pinDigitInput")?.focus();
}

async function toggleBiometricSetting() {
    if (window.PublicKeyCredential) {
        try {
            const newCredential = await navigator.credentials.create({
                publicKey: {
                    challenge: new Uint8Array(32),
                    rp: { name: "ARAF Intelligence" },
                    user: {
                        id: new TextEncoder().encode(auth.currentUser?.uid || "araf_user"),
                        name: auth.currentUser?.email || "trader@araf.com",
                        displayName: SESSION_VAULT.activeUser?.fullName || "ARAF Trader"
                    },
                    pubKeyCredParams: [{ alg: -7, type: "public-key" }],
                    timeout: 60000,
                    attestation: "direct"
                }
            });
            if (newCredential) {
                localStorage.setItem("ARAF_BIOMETRIC_ENABLED", "true");
                const btn = document.getElementById("btnBiometricToggle");
                if (btn) { btn.textContent = "ENABLED (ACTIVE)"; btn.className = "px-3 py-1.5 rounded-xl bg-[#00d09c] text-white font-black text-[10px]"; }
                alert("✅ Biometric Fingerprint Security successfully enabled!");
                return;
            }
        } catch (e) {}
    }
    alert("WebAuthn biometric registration not supported on this browser/device.");
}

async function handlePinSubmitUnlock() {
    const errMsg = document.getElementById("unlockErrorMsg");
    const inputPin = document.getElementById("pinDigitInput")?.value.trim() || "";
    if (inputPin.length !== 6 || !/^\d{6}$/.test(inputPin)) {
        errMsg.textContent = "Please enter valid 6-digit numeric PIN";
        errMsg.classList.remove("hidden");
        return;
    }
    if (!auth.currentUser) {
        errMsg.textContent = "Session expired. Please login again.";
        errMsg.classList.remove("hidden");
        return;
    }

    errMsg.classList.add("hidden");
    const btn = document.getElementById("btnSubmitUnlock");
    if (btn) { btn.disabled = true; btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin mr-1"></i> Verifying Server PIN...`; }

    try {
        const res = await secureApiCall("verify_pin", {
            method: "POST",
            jsonBody: { pin: inputPin }
        });
        if (res.ok && res.data?.success && res.data?.pinToken) {
            SESSION_VAULT.pinSessionToken = res.data.pinToken;
            sessionStorage.setItem("ARAF_PIN_SESSION", res.data.pinToken);
            document.getElementById("pinDigitInput").value = "";
            unlockTerminalSuccess();
        } else {
            errMsg.textContent = res.data?.message || "Wrong PIN!";
            errMsg.classList.remove("hidden");
            document.getElementById("pinDigitInput").value = "";
            if (navigator.vibrate) navigator.vibrate(200);
        }
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = `<i class="fa-solid fa-lock-open mr-1"></i> <span>Unlock App</span>`; }
    }
}

async function unlockTerminalSuccess() {
    const kycStat = SESSION_VAULT.activeUser?.kycStatus;
    if (SESSION_VAULT.activeUser && kycStat !== "APPROVED" && kycStat !== "VERIFIED_COMPLIANT") {
        showPendingAdminHoldScreen(SESSION_VAULT.activeUser);
        return;
    }
    if (SESSION_VAULT.activeUser && !isSubscriptionActive(SESSION_VAULT.activeUser)) {
        isTerminalUnlocked = false;
        showSubscriptionPaywall();
        return;
    }

    if (auth.currentUser) {
        const [riskReady, automationReady] = await Promise.all([
            syncServerRiskState(),
            syncAutoTradeState()
        ]);
        if (!riskReady || !automationReady) {
            isTerminalUnlocked = false;
            checkHardKillSwitchLock();
            return;
        }
    }
    isTerminalUnlocked = true;
    if (!backendOpportunityPollTimer) {
        backendOpportunityPollTimer = setInterval(pollBackendOpportunities, 3000);
    }
    if (!backendAutomationScanTimer) {
        void runBackendAutomationScan();
        backendAutomationScanTimer = setInterval(runBackendAutomationScan, 45000);
    }
    document.getElementById("quickUnlockModal")?.classList.add("hidden");
    document.getElementById("setupPinModal")?.classList.add("hidden");
    document.getElementById("authKycModal")?.classList.add("hidden");

    if (SESSION_VAULT.savedCloudUser) {
        const d = SESSION_VAULT.savedCloudUser;
        SESSION_VAULT.activeUser = d.profile;
        if (d.preferences) {
            SESSION_VAULT.voiceLang = d.preferences.voiceLang || "en-US";
            SESSION_VAULT.newsLang = d.preferences.newsLang || "en-US";
            SESSION_VAULT.aiChatLang = d.preferences.aiChatLang || "en-US";

            const langDropdown = document.getElementById("settingVoiceLanguage");
            if (langDropdown) langDropdown.value = SESSION_VAULT.voiceLang;
            const chatDropdown = document.getElementById("aiChatLangSelect");
            if (chatDropdown) chatDropdown.value = SESSION_VAULT.aiChatLang;
        }
        if (Array.isArray(d.tradeHistory)) {
            SESSION_VAULT.tradeHistory = d.tradeHistory;
            renderTradeHistory();
        }
    }

    const name = SESSION_VAULT.activeUser?.fullName || "ARAF TRADER";
    const traderUid = SESSION_VAULT.activeUser?.traderUid || "PENDING";
    document.getElementById("hudUserStatusText").innerHTML =
        `<span class="w-1.5 h-1.5 rounded-full bg-[#00d09c] animate-pulse mr-1"></span> <span id="hudDynamicUserName">${sanitizeCleanText(name)}</span> • App Active`;

    const profileNameEl = document.getElementById("settingsProfileName");
    const profileUidEl = document.getElementById("settingsProfileUid");
    const profileKycEl = document.getElementById("settingsKycBadge");

    if (profileNameEl) profileNameEl.textContent = name.toUpperCase();
    if (profileUidEl) profileUidEl.textContent = traderUid;
    if (profileKycEl) profileKycEl.textContent = `KYC: ${SESSION_VAULT.activeUser?.kycStatus || "PENDING"}`;

    updateVipTrialBadgeUi();
    await refreshBrokerVaultStatus();
    await Promise.allSettled([
        syncRealBrokerBalances(),
        initSyncPositionsToMemory(),
        syncBrokerHoldingsAndOrders(),
        fetchMasterOrPublicIndianFeed(),
        syncCoinDcxOrBinanceFeed(),
        syncAutoTradeState()
    ]);
    checkHardKillSwitchLock();

    const welcomeVoice = (SESSION_VAULT.voiceLang === "bn-IN")
        ? "Welcome trader, your Pro Trading app is unlocked. Today only disciplined, rule-based trades."
        : "Pro trading app unlocked. Ready for disciplined execution.";
    explainTextFriendly(welcomeVoice, "App Live");
    startPeriodicVoiceScanner();
}
async function handleSaveInitialPinAndBiometric() {
    const p1 = document.getElementById("newPinInput")?.value.trim() || "";
    const p2 = document.getElementById("confirmPinInput")?.value.trim() || "";
    const err = document.getElementById("setupPinErrorMsg");
    const btn = document.getElementById("btnSaveInitialPin");

    if (p1.length !== 6 || !/^\d{6}$/.test(p1)) {
        err.textContent = "PIN must be exactly 6 numeric digits";
        err.classList.remove("hidden");
        return;
    }
    if (p1 !== p2) {
        err.textContent = "PIN entries do not match!";
        err.classList.remove("hidden");
        return;
    }
    if (!auth.currentUser) {
        err.textContent = "Authentication required first.";
        err.classList.remove("hidden");
        return;
    }

    err.classList.add("hidden");
    if (btn) { btn.disabled = true; btn.textContent = "Securing PIN on Server..."; }

    try {
        const res = await secureApiCall("set_master_pin", {
            method: "POST",
            jsonBody: { pin: p1 }
        });
        if (!res.ok || !res.data?.success) {
            err.textContent = res.data?.message || "Failed to save PIN on server.";
            err.classList.remove("hidden");
            return;
        }
        SESSION_VAULT.hasServerPin = true;
        if (res.data.pinToken) {
            SESSION_VAULT.pinSessionToken = res.data.pinToken;
            sessionStorage.setItem("ARAF_PIN_SESSION", res.data.pinToken);
        }
        document.getElementById("newPinInput").value = "";
        document.getElementById("confirmPinInput").value = "";
        document.getElementById("setupPinModal")?.classList.add("hidden");

        const st = SESSION_VAULT.activeUser?.kycStatus;
        if (st === "APPROVED" || st === "VERIFIED_COMPLIANT") {
            unlockTerminalSuccess();
        } else if (SESSION_VAULT.activeUser) {
            showPendingAdminHoldScreen(SESSION_VAULT.activeUser);
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Save PIN Securely"; }
    }
}

function handleSwitchAccountOrDevice() {
    handleSessionWipeLogout();
}

function dismissQuickUnlockModal() {
    if (isTerminalUnlocked && SESSION_VAULT.pinSessionToken) {
        document.getElementById("quickUnlockModal")?.classList.add("hidden");
    } else {
        const err = document.getElementById("unlockErrorMsg");
        if (err) {
            err.textContent = "Please unlock app with your 6-digit PIN.";
            err.classList.remove("hidden");
        }
    }
}

auth.onAuthStateChanged(async (user) => {
    if (user) {
        isTerminalUnlocked = false;
        SESSION_VAULT.userKey = user.uid;
        const [, riskReady, autoTradeReady] = await Promise.all([
            syncTerminalPreferences(),
            syncServerRiskState(),
            syncAutoTradeState()
        ]);
        if (!riskReady || !autoTradeReady) {
            isTerminalUnlocked = false;
            document.getElementById("hudUserStatusText").textContent =
                "Server risk/kill-switch status unavailable. Trading remains blocked.";
            return;
        }
        renderAutoTradeUi();
        checkHardKillSwitchLock();
        let loadRes = await ArafCloudMaster.loadUserFromAnyDevice(false);

        if (loadRes.status === "ERROR" && loadRes.httpStatus === 401) {
            loadRes = await ArafCloudMaster.loadUserFromAnyDevice(true);
        }

        if (loadRes.status === "ERROR") {
            const errBox = document.getElementById("authCustomErrorMsg");
            if (errBox) {
                errBox.textContent = `Server Error (${loadRes.httpStatus || 0}): ${loadRes.message || "Check connection or re-login."}`;
                errBox.classList.remove("hidden");
            }
            document.getElementById("hudUserStatusText").textContent = "Server unreachable. Check connection & reload.";
            return;
        }

        const cloudData = loadRes.fullData;
        if (cloudData?.profile?.isActive === false) {
            SESSION_VAULT.activeUser = cloudData.profile;
            showPendingAdminHoldScreen(cloudData.profile);
            alert("Your account access is disabled. Contact ARAF support for assistance.");
            return;
        }
        if (cloudData && cloudData.profile && cloudData.profile.kycStatus) {
            SESSION_VAULT.savedCloudUser = cloudData;
            SESSION_VAULT.activeUser = cloudData.profile;

            const st = cloudData.profile.kycStatus;
            updateSubscriptionStatusUi();
            if (cloudData.profile.isActive === false) {
                showPendingAdminHoldScreen(cloudData.profile);
                return;
            }
            if (st !== "APPROVED" && st !== "VERIFIED_COMPLIANT") {
                showPendingAdminHoldScreen(cloudData.profile);
                return;
            }
            if (!isSubscriptionActive(cloudData.profile)) {
                showSubscriptionPaywall();
                return;
            }

            if (SESSION_VAULT.hasServerPin) {
                const savedPinTok = sessionStorage.getItem("ARAF_PIN_SESSION");
                if (savedPinTok) {
                    SESSION_VAULT.pinSessionToken = savedPinTok;
                    unlockTerminalSuccess();
                } else if (isTerminalUnlocked && SESSION_VAULT.pinSessionToken) {
                    unlockTerminalSuccess();
                } else {
                    document.getElementById("unlockWelcomeTitle").textContent = `Welcome, ${cloudData.profile?.fullName || "Trader"}!`;
                    document.getElementById("quickUnlockModal")?.classList.remove("hidden");
                    document.getElementById("authKycModal")?.classList.add("hidden");
                }
            } else {
                document.getElementById("authKycModal")?.classList.add("hidden");
                document.getElementById("setupPinModal")?.classList.remove("hidden");
            }
        } else {
            document.getElementById("authStepPhone")?.classList.add("hidden");
            document.getElementById("authStepKyc")?.classList.remove("hidden");
            document.getElementById("authKycModal")?.classList.remove("hidden");
            if (!user.emailVerified && !isServerOtpVerifiedThisSession) {
                document.getElementById("firebaseEmailVerifyBackupBox")?.classList.remove("hidden");
            }
        }
    } else {
        if (subscriptionStatusPollTimer) clearInterval(subscriptionStatusPollTimer);
        subscriptionStatusPollTimer = null;
        isTerminalUnlocked = false;
        serverRiskStateLoaded = false;
        serverAutoTradeStateLoaded = false;
        riskPnlSyncFailed = true;
        SESSION_VAULT.pinSessionToken = null;
        if (backendOpportunityPollTimer) clearInterval(backendOpportunityPollTimer);
        if (backendAutomationScanTimer) clearInterval(backendAutomationScanTimer);
        backendOpportunityPollTimer = null;
        backendAutomationScanTimer = null;
        backendAutomationScanBusy = false;
        OPPORTUNITY_QUEUE = [];
        dismissOpportunityPopup();
        sessionStorage.removeItem("ARAF_PIN_SESSION");
        document.getElementById("quickUnlockModal")?.classList.add("hidden");
        document.getElementById("authKycModal")?.classList.remove("hidden");
    }
});

let pendingAuthSession = { email: "", pass: "", mode: "SIGNUP" };
let recoverySessionState = { email: "", mode: "PASSWORD_RESET" };

function handleSafeAuthModalClose() {
    if (!isTerminalUnlocked || !auth.currentUser || !SESSION_VAULT.pinSessionToken) {
        const err = document.getElementById("authCustomErrorMsg");
        if (err) {
            err.textContent = "Access Denied: Login & Admin KYC Approval required to use app .";
            err.classList.remove("hidden");
        }
        return;
    }
    const st = SESSION_VAULT.activeUser?.kycStatus;
    if (st !== "APPROVED" && st !== "VERIFIED_COMPLIANT") return;
    document.getElementById("authKycModal")?.classList.add("hidden");
}

function handleAuthBackNavigation() {
    const kycStep = document.getElementById("authStepKyc");
    const pendingStep = document.getElementById("authStepPending");
    const formSign = document.getElementById("formNewRegistration");

    if (kycStep && !kycStep.classList.contains("hidden")) { backFromKycToAuthTabs(); return; }
    if (pendingStep && !pendingStep.classList.contains("hidden")) {
        pendingStep.classList.add("hidden");
        document.getElementById("authStepPhone")?.classList.remove("hidden");
        switchAuthMode("LOGIN");
        return;
    }
    if (formSign && !formSign.classList.contains("hidden")) { switchAuthMode("LOGIN"); return; }
}

function backFromKycToAuthTabs() {
    document.getElementById("authStepKyc")?.classList.add("hidden");
    document.getElementById("authStepPending")?.classList.add("hidden");
    document.getElementById("authStepPhone")?.classList.remove("hidden");
    switchAuthMode("LOGIN");
}

function switchAuthMode(mode) {
    const tabLog = document.getElementById("tabAuthLogin");
    const tabSign = document.getElementById("tabAuthSignup");
    const formLog = document.getElementById("formClientIdLogin");
    const formSign = document.getElementById("formNewRegistration");
    const err = document.getElementById("authCustomErrorMsg");
    if (err) err.classList.add("hidden");

    if (mode === "LOGIN") {
        tabLog.className = "flex-1 py-2 rounded-lg groww-btn-green transition shadow-sm font-bold";
        tabSign.className = "flex-1 py-2 rounded-lg text-slate-500 hover:text-slate-900 transition font-bold";
        formLog.classList.remove("hidden");
        formSign.classList.add("hidden");
    } else {
        tabSign.className = "flex-1 py-2 rounded-lg groww-btn-green transition shadow-sm font-bold";
        tabLog.className = "flex-1 py-2 rounded-lg text-slate-500 hover:text-slate-900 transition font-bold";
        formSign.classList.remove("hidden");
        formLog.classList.add("hidden");
    }
}

async function handleTraderUidLogin() {
    const identifier = document.getElementById("loginTraderUid")?.value.trim();
    const pass = document.getElementById("loginPassword")?.value.trim();
    const errBox = document.getElementById("authCustomErrorMsg");
    const btn = document.getElementById("btnLoginUidSubmit");
    if (errBox) errBox.classList.add("hidden");

    if (!identifier || identifier.length < 5) {
        errBox.textContent = "Enter valid Trader UID or Email!";
        errBox.classList.remove("hidden");
        return;
    }
    if (!pass || pass.length < 6) {
        errBox.textContent = "Password must be at least 6 characters!";
        errBox.classList.remove("hidden");
        return;
    }

    if (btn) { btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin mr-1"></i> Authenticating...`; btn.disabled = true; }

    try {
        let targetEmail = identifier.toLowerCase();
        if (!identifier.includes("@")) {
            const res = await secureApiCall("login_with_trader_uid", {
                method: "POST",
                jsonBody: { traderUid: identifier.toUpperCase(), password: pass }
            });
            if (!res.ok || !res.data?.email) {
                throw new Error(res.data?.message || "Invalid Trader UID or Password!");
            }
            targetEmail = res.data.email;
        }
        await auth.signInWithEmailAndPassword(targetEmail, pass);
    } catch (err) {
        if (errBox) {
            errBox.textContent = "Error: " + (err.message || "Authentication failed.");
            errBox.classList.remove("hidden");
        }
    } finally {
        if (btn) { btn.innerHTML = `<i class="fa-solid fa-lock-open mr-1"></i> <span>Login Securely</span>`; btn.disabled = false; }
    }
}

async function handleNewUserSignup() {
    const email = document.getElementById("signupEmailInput")?.value.trim().toLowerCase();
    const pass = document.getElementById("signupPasswordInput")?.value.trim();
    const confirmPass = document.getElementById("signupConfirmPasswordInput")?.value.trim();
    const errBox = document.getElementById("authCustomErrorMsg");
    const signupBtn = document.getElementById("btnSignupSubmit");

    if (errBox) errBox.classList.add("hidden");
    const showError = (msg) => { if (errBox) { errBox.textContent = msg; errBox.classList.remove("hidden"); } };

    if (!email || !email.includes("@")) { showError("Please enter a valid Gmail / Email address!"); return; }
    if (!pass || pass.length < 6) { showError("Password must be at least 6 characters!"); return; }
    if (!confirmPass || pass !== confirmPass) { showError("Passwords do not match!"); return; }

    pendingAuthSession = { email, pass, mode: "SIGNUP" };
    if (signupBtn) { signupBtn.innerHTML = `<i class="fa-solid fa-spinner animate-spin mr-1"></i> Sending OTP...`; signupBtn.disabled = true; }

    try {
        const res = await secureApiCall("send_real_otp", {
            method: "POST",
            jsonBody: { to: email, purpose: "SIGNUP" }
        });

        if (res.ok && res.data?.success) {
            document.getElementById("authKycModal")?.classList.add("hidden");
            const recoveryModal = document.getElementById("accountRecoveryModal");
            if (recoveryModal) recoveryModal.classList.remove("hidden");
            document.getElementById("recoveryStepEmail")?.classList.add("hidden");
            document.getElementById("recoveryStepOtp")?.classList.remove("hidden");
            document.getElementById("recoveryStepReset")?.classList.add("hidden");
            document.getElementById("recoveryErrorMsg")?.classList.add("hidden");
            document.getElementById("displayTargetEmail").textContent = email;
            document.getElementById("recoveryOtpInput")?.focus();
        } else if (res.data?.fallbackFirebaseRecommended) {
            const userCred = await auth.createUserWithEmailAndPassword(email, pass);
            await userCred.user.sendEmailVerification();
            alert("OTP Bridge unavailable right now — Firebase Verification Link sent to your inbox! Please verify your email and complete KYC.");
            document.getElementById("firebaseEmailVerifyBackupBox")?.classList.remove("hidden");
        } else {
            showError(res.data?.message || "Failed to send OTP.");
        }
    } catch (e) {
        showError(e.message || "Registration failed.");
    } finally {
        if (signupBtn) { signupBtn.innerHTML = `<i class="fa-solid fa-user-plus mr-1"></i> <span>Verify Gmail OTP & Create Account</span>`; signupBtn.disabled = false; }
    }
}

async function sendFirebaseVerificationBackup() {
    if (!auth.currentUser) return;
    try {
        await auth.currentUser.sendEmailVerification();
        alert("Verification link sent to " + auth.currentUser.email);
    } catch (e) {
        alert(e.message);
    }
}

async function recheckFirebaseEmailVerified() {
    if (!auth.currentUser) return;
    await auth.currentUser.reload();
    if (auth.currentUser.emailVerified) {
        await auth.currentUser.getIdToken(true);
        document.getElementById("firebaseEmailVerifyBackupBox")?.classList.add("hidden");
        alert("Email Verified! Now complete your KYC form.");
    } else {
        alert("Not verified yet. Please click the link sent to your inbox.");
    }
}

async function handleGoogleOneTapLogin() {
    try {
        const provider = new firebase.auth.GoogleAuthProvider();
        provider.setCustomParameters({ prompt: "select_account" });
        const res = await auth.signInWithPopup(provider);
        if (res?.user) {
            const kycName = document.getElementById("kycFullName");
            if (kycName) kycName.value = (res.user.displayName || "").toUpperCase();
        }
    } catch (e) {
        if (e.code === "auth/popup-blocked") {
            await auth.signInWithRedirect(new firebase.auth.GoogleAuthProvider());
        } else {
            alert(`Google Authentication: ${e.message}`);
        }
    }
}

function handleForgotPinReset() {
    document.getElementById("quickUnlockModal")?.classList.add("hidden");
    openAccountRecoveryModal("PIN_RESET");
}

function openAccountRecoveryModal(mode = "PASSWORD_RESET") {
    document.getElementById("authKycModal")?.classList.add("hidden");
    document.getElementById("quickUnlockModal")?.classList.add("hidden");
    document.getElementById("setupPinModal")?.classList.add("hidden");
    document.getElementById("accountRecoveryModal")?.classList.remove("hidden");

    pendingAuthSession.mode = mode;
    recoverySessionState.mode = mode;
    document.getElementById("recoveryStepEmail")?.classList.remove("hidden");
    document.getElementById("recoveryStepOtp")?.classList.add("hidden");
    document.getElementById("recoveryStepReset")?.classList.add("hidden");
    document.getElementById("recoveryErrorMsg")?.classList.add("hidden");

    const btnPass = document.getElementById("btnSendOtp");
    const btnPin = document.getElementById("btnSendPinOtp");
    if (mode === "PIN_RESET" && auth.currentUser) {
        btnPass?.classList.add("hidden");
        btnPin?.classList.remove("hidden");
    } else {
        btnPass?.classList.remove("hidden");
        btnPin?.classList.add("hidden");
    }

    const emailInput = document.getElementById("recoveryEmailInput");
    if (emailInput) {
        emailInput.value = auth.currentUser?.email || SESSION_VAULT.activeUser?.email || "";
        emailInput.focus();
    }
}

function closeAccountRecoveryModal() {
    document.getElementById("accountRecoveryModal")?.classList.add("hidden");
    if (auth.currentUser && SESSION_VAULT.hasServerPin) {
        document.getElementById("quickUnlockModal")?.classList.remove("hidden");
    } else {
        document.getElementById("authKycModal")?.classList.remove("hidden");
    }
}

function backToSignupEmailStep() {
    document.getElementById("accountRecoveryModal")?.classList.add("hidden");
    document.getElementById("authKycModal")?.classList.remove("hidden");
    switchAuthMode("SIGNUP");
}

async function sendRecoveryOtpToEmail() {
    const email = document.getElementById("recoveryEmailInput")?.value.trim().toLowerCase();
    const err = document.getElementById("recoveryErrorMsg");
    const btn = document.getElementById("btnSendOtp");
    if (!email || !email.includes("@")) {
        err.textContent = "Please enter a valid email address!";
        err.classList.remove("hidden");
        return;
    }
    err.classList.add("hidden");
    btn.disabled = true;
    try {
        await auth.sendPasswordResetEmail(email);
        alert("If an account exists for this email, a Firebase Password Reset link has been sent to your inbox.");
        closeAccountRecoveryModal();
    } catch (e) {
        err.textContent = "Unable to process password reset request right now.";
        err.classList.remove("hidden");
    } finally {
        btn.disabled = false;
    }
}

async function sendPinResetServerOtp() {
    const email = (auth.currentUser?.email || document.getElementById("recoveryEmailInput")?.value || "").trim().toLowerCase();
    const err = document.getElementById("recoveryErrorMsg");
    if (!email) { err.textContent = "Valid email required."; err.classList.remove("hidden"); return; }
    err.classList.add("hidden");

    const res = await secureApiCall("send_real_otp", {
        method: "POST",
        jsonBody: { to: email, purpose: "PIN_RESET" }
    });
    if (res.ok && res.data?.success) {
        recoverySessionState.email = email;
        pendingAuthSession.mode = "PIN_RESET";
        document.getElementById("recoveryStepEmail")?.classList.add("hidden");
        document.getElementById("recoveryStepOtp")?.classList.remove("hidden");
        document.getElementById("displayTargetEmail").textContent = email;
    } else {
        err.textContent = res.data?.message || "Failed to send PIN reset OTP.";
        err.classList.remove("hidden");
    }
}

async function verifyRecoveryOtp() {
    const entered = document.getElementById("recoveryOtpInput")?.value.trim();
    const err = document.getElementById("recoveryErrorMsg");
    const btn = document.getElementById("btnVerifyOtpSubmit");
    const targetEmail = pendingAuthSession.mode === "SIGNUP" ? pendingAuthSession.email : recoverySessionState.email;
    const purpose = pendingAuthSession.mode === "SIGNUP" ? "SIGNUP" : "PIN_RESET";

    if (!entered || entered.length !== 6) {
        err.textContent = "Enter the 6-digit OTP sent to your email.";
        err.classList.remove("hidden");
        return;
    }

    if (btn) { btn.textContent = "Verifying OTP..."; btn.disabled = true; }
    try {
        const res = await secureApiCall("verify_server_otp", {
            method: "POST",
            jsonBody: { email: targetEmail, code: entered, purpose }
        });
        if (!res.ok || !res.data?.success) {
            err.textContent = res.data?.message || "Invalid or expired OTP!";
            err.classList.remove("hidden");
            return;
        }
        err.classList.add("hidden");

        if (purpose === "SIGNUP" && pendingAuthSession.email) {
            isServerOtpVerifiedThisSession = true;
            await auth.createUserWithEmailAndPassword(pendingAuthSession.email, pendingAuthSession.pass);
            document.getElementById("accountRecoveryModal")?.classList.add("hidden");
            const kycName = document.getElementById("kycFullName");
            if (kycName) kycName.value = pendingAuthSession.email.split("@")[0].toUpperCase();
            document.getElementById("authKycModal")?.classList.remove("hidden");
            document.getElementById("authStepPhone")?.classList.add("hidden");
            document.getElementById("authStepKyc")?.classList.remove("hidden");
            document.getElementById("firebaseEmailVerifyBackupBox")?.classList.add("hidden");
            return;
        }

        document.getElementById("recoveryStepOtp")?.classList.add("hidden");
        document.getElementById("recoveryStepReset")?.classList.remove("hidden");
        document.getElementById("recoveredTraderUidText").textContent = SESSION_VAULT.activeUser?.traderUid || targetEmail;
    } catch (e) {
        err.textContent = e.message || "Verification failed.";
        err.classList.remove("hidden");
    } finally {
        if (btn) { btn.textContent = "Verify OTP Code"; btn.disabled = false; }
    }
}

async function finalizeAccountCredentialsReset() {
    const newPin = document.getElementById("resetNewPin")?.value.trim() || "";
    const err = document.getElementById("recoveryErrorMsg");
    if (newPin.length !== 6 || !/^\d{6}$/.test(newPin)) {
        err.textContent = "Master PIN must be 6 digits!";
        err.classList.remove("hidden");
        return;
    }
    const res = await secureApiCall("set_master_pin", { method: "POST", jsonBody: { pin: newPin } });
    if (!res.ok || !res.data?.success) {
        err.textContent = res.data?.message || "Failed to update PIN.";
        err.classList.remove("hidden");
        return;
    }
    if (res.data.pinToken) {
        SESSION_VAULT.pinSessionToken = res.data.pinToken;
        sessionStorage.setItem("ARAF_PIN_SESSION", res.data.pinToken);
    }
    document.getElementById("resetNewPin").value = "";
    document.getElementById("accountRecoveryModal")?.classList.add("hidden");
    alert("Master PIN Updated Securely!");
    unlockTerminalSuccess();
}

function showPendingAdminHoldScreen(profileObj) {
    document.getElementById("quickUnlockModal")?.classList.add("hidden");
    document.getElementById("setupPinModal")?.classList.add("hidden");
    document.getElementById("authKycModal")?.classList.remove("hidden");
    document.getElementById("authStepPhone")?.classList.add("hidden");
    document.getElementById("authStepKyc")?.classList.add("hidden");
    document.getElementById("authStepPending")?.classList.remove("hidden");

    const revoked = profileObj.isActive === false;
    document.querySelector("#authStepPending h3").textContent =
        revoked ? "Account Access Revoked" : "Pending Admin Approval";
    document.querySelector("#authStepPending > p").textContent = revoked
        ? "An administrator has revoked this account's access. Contact ARAF support if you believe this is a mistake."
        : "Your compliance profile has been submitted to ARAF Admin Desk. Your app will unlock immediately upon verification.";
    document.getElementById("pendingUserKycStatus").textContent =
        revoked ? "ACCESS_REVOKED" : (profileObj.kycStatus || "PENDING_ADMIN_REVIEW");
    document.getElementById("pendingUserName").textContent = profileObj.fullName || "-";
    document.getElementById("pendingUserUid").textContent = profileObj.traderUid || "-";
    document.getElementById("pendingUserPan").textContent = profileObj.panMasked || profileObj.pan || "-";
};

async function handleSubmitForVerification() {
    const errBox = document.getElementById("kycErrorBanner");
    if (errBox) errBox.classList.add("hidden");
    const btn = document.getElementById("btnSubmitKyc");

    const fullName = document.getElementById("kycFullName")?.value.trim();
    const dob = document.getElementById("kycDob")?.value;
    const phone = document.getElementById("kycPhone")?.value.trim();
    const pan = document.getElementById("kycPanNumber")?.value.trim().toUpperCase();
    const aadhaarLast4 = document.getElementById("kycAadhaarLast4")?.value.trim();
    const incomeBracket = document.getElementById("kycIncome")?.value;
    const experience = document.getElementById("kycExperience")?.value;
    const authorizedSegment = document.getElementById("kycSegment")?.value;
    const riskAgreed = document.getElementById("kycRiskConsent")?.checked;

    const showKycError = (msg) => { if (errBox) { errBox.textContent = msg; errBox.classList.remove("hidden"); } };
    if (!fullName || fullName.length < 3) { showKycError("Full legal name required!"); return; }
    if (!pan || !/^[A-Z]{5}[0-9]{4}[A-Z]{1}$/.test(pan)) { showKycError("Enter valid 10-digit PAN (e.g. ABCDE1234F)!"); return; }
    if (!aadhaarLast4 || !/^\d{4}$/.test(aadhaarLast4)) { showKycError("Enter last 4 digits of Aadhaar!"); return; }
    if (!riskAgreed) { showKycError("Declaration checkbox required!"); return; }

    if (btn) { btn.disabled = true; btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin mr-1"></i> Submitting to Server...`; }

    try {
        const res = await secureApiCall("submit_kyc", {
            method: "POST",
            jsonBody: { fullName, dob, phone, pan, aadhaarLast4, incomeBracket, experience, authorizedSegment }
        });

        if (!res.ok || !res.data?.success) {
            if (res.status === 403 && !auth.currentUser?.emailVerified) {
                document.getElementById("firebaseEmailVerifyBackupBox")?.classList.remove("hidden");
            }
            showKycError(res.data?.message || "KYC submission failed.");
            return;
        }

        const traderUid = res.data.traderUid;
        SESSION_VAULT.activeUser = {
            fullName: fullName.toUpperCase(),
            traderUid,
            panMasked: `${pan.slice(0, 2)}XXXXX${pan.slice(7)}`,
            kycStatus: "PENDING_ADMIN_REVIEW"
        };

        document.getElementById("authKycModal")?.classList.add("hidden");
        alert(`KYC Submitted! Your Unique Trader UID: ${traderUid} | Now set your 6-Digit Master PIN.`);
        document.getElementById("setupPinModal")?.classList.remove("hidden");
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = `<i class="fa-solid fa-file-shield"></i> <span>Submit Verification to Admin Review</span>`; }
    }
}

// =====================================================================
// Account Deletion (Module 9 - GDPR Compliant)
// =====================================================================

let accountDeletionCountdownTimer = null;
let accountDeletionCountdownSeconds = 15;

async function requestAccountDeletion() {
    if (!auth.currentUser) return;
    
    const res = await secureApiCall("account-deletion/request", {
        method: "POST",
        jsonBody: {}
    });
    
    if (!res.ok || !res.data?.success) {
        alert(res.data?.message || "Failed to request account deletion");
        return;
    }
    
    // Show 15-second countdown modal
    showAccountDeletionCountdownModal(res.data.cancellation_window_ends_at);
}

function showAccountDeletionCountdownModal(cancellationWindowEndsAt) {
    const modal = document.getElementById("accountDeletionCountdownModal");
    const countdownEl = document.getElementById("accountDeletionCountdown");
    
    if (!modal || !countdownEl) return;
    
    modal.classList.remove("hidden");
    
    const endTime = new Date(cancellationWindowEndsAt).getTime();
    accountDeletionCountdownSeconds = 15;
    
    const updateCountdown = () => {
        const now = Date.now();
        const remaining = Math.max(0, (endTime - now) / 1000);
        
        if (countdownEl) {
            countdownEl.textContent = remaining.toFixed(2);
        }
        
        if (remaining <= 0) {
            clearInterval(accountDeletionCountdownTimer);
            accountDeletionCountdownTimer = null;
            dismissAccountDeletionCountdown();
            showAccountDeletionConfirmModal();
        }
    };
    
    updateCountdown();
    accountDeletionCountdownTimer = setInterval(updateCountdown, 50);
}

function dismissAccountDeletionCountdown() {
    const modal = document.getElementById("accountDeletionCountdownModal");
    if (modal) modal.classList.add("hidden");
    if (accountDeletionCountdownTimer) {
        clearInterval(accountDeletionCountdownTimer);
        accountDeletionCountdownTimer = null;
    }
}

async function cancelAccountDeletion() {
    dismissAccountDeletionCountdown();
    
    const res = await secureApiCall("account-deletion/cancel", {
        method: "POST",
        jsonBody: {}
    });
    
    if (res.ok && res.data?.success) {
        alert("Account deletion cancelled successfully");
    } else {
        alert(res.data?.message || "Failed to cancel deletion request");
    }
}

function showAccountDeletionConfirmModal() {
    const modal = document.getElementById("accountDeletionConfirmModal");
    if (modal) modal.classList.remove("hidden");
}

function dismissAccountDeletionConfirm() {
    const modal = document.getElementById("accountDeletionConfirmModal");
    if (modal) modal.classList.add("hidden");
}

async function confirmAccountDeletion() {
    const modal = document.getElementById("accountDeletionConfirmModal");
    if (modal) modal.classList.add("hidden");
    
    alert("Account deletion confirmed. Your request is now pending admin approval.");
    
    // Show pending lock screen
    showAccountDeletionPendingModal();
}

function showAccountDeletionPendingModal() {
    const modal = document.getElementById("accountDeletionPendingModal");
    if (modal) modal.classList.remove("hidden");
}

function dismissAccountDeletionConfirm() {
    const modal = document.getElementById("accountDeletionConfirmModal");
    if (modal) modal.classList.add("hidden");
}

async function checkAccountDeletionStatus() {
    if (!auth.currentUser) return;
    
    const res = await secureApiCall("account-deletion/status", {
        method: "GET"
    });
    
    if (!res.ok || !res.data) return;
    
    const status = res.data;
    
    if (status.deletion_status === "DELETION_PENDING") {
        showAccountDeletionPendingModal();
    }
}

async function recheckAdminApprovalStatus() {
    if (!auth.currentUser) return;
    const loadRes = await ArafCloudMaster.loadUserFromAnyDevice();
    const cloudData = loadRes.fullData;
    if (cloudData && (cloudData.profile?.kycStatus === "APPROVED" || cloudData.profile?.kycStatus === "VERIFIED_COMPLIANT")) {
        SESSION_VAULT.savedCloudUser = cloudData;
        SESSION_VAULT.activeUser = cloudData.profile;
        if (!isSubscriptionActive(cloudData.profile)) {
            showSubscriptionPaywall();
            return;
        }
        alert("Congratulations! Your KYC is approved. The app is now unlocking.");
        if (SESSION_VAULT.hasServerPin) {
            if (SESSION_VAULT.pinSessionToken) unlockTerminalSuccess();
            else {
                document.getElementById("authKycModal")?.classList.add("hidden");
                document.getElementById("quickUnlockModal")?.classList.remove("hidden");
            }
        } else {
            document.getElementById("authKycModal")?.classList.add("hidden");
            document.getElementById("setupPinModal")?.classList.remove("hidden");
        }
    } else {
        alert("Your profile is still pending admin review. Unlock occurs after approval.");
    }
}

async function handleSessionWipeLogout() {
    try {
        if (subscriptionStatusPollTimer) clearInterval(subscriptionStatusPollTimer);
        subscriptionStatusPollTimer = null;
        if (auth.currentUser) {
            await secureApiCall("logout_session", { method: "POST" }).catch(() => {});
        }
        if (binanceWs) { binanceWs.onclose = null; binanceWs.close(); binanceWs = null; }
        if (binanceReconnectTimer) clearTimeout(binanceReconnectTimer);
        if (periodicVoiceIntervalId) clearInterval(periodicVoiceIntervalId);
        if (marketPollIntervalId) clearInterval(marketPollIntervalId);
        if (freshnessIntervalId) clearInterval(freshnessIntervalId);
        if (sqOffIntervalId) clearInterval(sqOffIntervalId);
        stopAiVoice();

        await auth.signOut().catch(() => {});
        sessionStorage.removeItem("ARAF_SESSION_UNLOCKED");
        sessionStorage.removeItem("ARAF_PIN_SESSION");
        ["ARAF_PERMANENT_TRADER_UID", "ARAF_LAST_ACTIVE_USERKEY", "ARAF_USER_EMAIL", "ARAF_MASTER_PIN_HASH"].forEach(k => localStorage.removeItem(k));

        SESSION_VAULT.userKey = null;
        SESSION_VAULT.activeUser = null;
        SESSION_VAULT.savedCloudUser = null;
        SESSION_VAULT.pinSessionToken = null;
        SESSION_VAULT.shoonyaLinked = false;
        SESSION_VAULT.coindcxLinked = false;
        SESSION_VAULT.activeBracketOrders = {};
        SESSION_VAULT.fastPositionsMap = {};
        applySettingsCredentialLock();
        applySettingsCredentialLock("shoonya", false);
        applySettingsCredentialLock("coindcx", false);

        location.reload();
    } catch (e) {
        location.reload();
    }
}

async function startSplashScreen() {
    const splash = document.getElementById("splashScreen");
    const status = document.getElementById("splashStatusText");
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4500);
    let connected = false;
    try {
        const response = await fetch(`${FASTAPI_BASE_URL}/health`, {
            method: "GET",
            cache: "no-store",
            signal: controller.signal
        });
        connected = response.ok;
    } catch (error) {
        connected = false;
    } finally {
        clearTimeout(timeoutId);
    }
    if (status) {
        status.textContent = connected
            ? "Secure backend connected"
            : "Backend unavailable • retrying in background";
        status.classList.toggle("text-emerald-300", connected);
        status.classList.toggle("text-amber-300", !connected);
    }
    await new Promise(resolve => setTimeout(resolve, connected ? 900 : 1800));
    if (splash) {
        splash.style.transition = "opacity 500ms cubic-bezier(0.16, 1, 0.3, 1)";
        splash.style.opacity = "0";
        setTimeout(() => {
            splash.remove();
            const appRoot = document.getElementById("appRoot");
            if (appRoot) appRoot.classList.remove("hidden");
        }, 500);
    } else {
        const appRoot = document.getElementById("appRoot");
        if (appRoot) appRoot.classList.remove("hidden");
    }
}

let arafChartInstance = null;
let arafCandleSeries = null;
let activeChartTimeframe = "15m";
let activeChartYahooSymbol = "";
let activeChartLastBar = null;
let activeChartIsCrypto = false;

const CHART_TF_SECONDS = { "15s": 15, "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400 };

function changeChartTimeframe(tf) {
    activeChartTimeframe = tf;
    terminalPreferences.chart_timeframe = tf;
    void saveTerminalPreferences();
    ["15s", "1m", "5m", "15m", "1h", "1d"].forEach(t => {
        const btn = document.getElementById(`tfBtn_${t}`);
        if (btn) btn.className = t === tf
            ? "px-2 py-1 rounded-lg bg-[#00d09c] text-white font-black"
            : "px-2 py-1 rounded-lg bg-slate-100 text-slate-600";
    });
    initTradingViewChart(currentActiveSymbol, activeChartYahooSymbol);
}

async function searchAndLoadInAppChart() {
    const raw = document.getElementById("inAppChartSearchInput")?.value.trim();
    if (!raw) return;
    const upper = raw.toUpperCase();

    if (upper.endsWith("USDT") || ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "PAXG"].includes(upper)) {
        const pair = upper.endsWith("USDT") ? upper : `${upper}USDT`;
        const exchPrefix = SESSION_VAULT.coindcxLinked ? "COINDCX" : "BINANCE";
        initTradingViewChart(`${exchPrefix}:${pair}`, "");
        return;
    }
    const res = await secureApiCall("search_stock", {
        method: "GET",
        query: `q=${encodeURIComponent(raw)}`
    });
    const data = res.ok ? res.data : null;
    if (data?.success && Array.isArray(data.items) && data.items.length > 0) {
        const hit = data.items[0];
        initTradingViewChart(`${hit.exchange}:${hit.orderKey}`, hit.yahooSymbol);
    } else {
        initTradingViewChart(`NSE:${upper}`, `${upper}.NS`);
    }
}

function updateLiveInAppChartCandle(orderKey, livePrice, eventTimeMs = Date.now()) {
    if (!arafCandleSeries || !activeChartLastBar || !Number.isFinite(livePrice) || livePrice <= 0) return;
    const rawSym = (currentActiveSymbol.includes(":") ? currentActiveSymbol.split(":")[1] : currentActiveSymbol).toUpperCase();
    const targetKey = String(orderKey || "").toUpperCase();
    if (rawSym !== targetKey && !rawSym.includes(targetKey) && !targetKey.includes(rawSym)) return;

    const tfSec = CHART_TF_SECONDS[activeChartTimeframe] || 900;
    const tzOffset = activeChartIsCrypto ? 0 : 19800;
    const nowSec = Math.floor(eventTimeMs / 1000) + tzOffset;
    const bucketTime = Math.floor(nowSec / tfSec) * tfSec;

    if (bucketTime > activeChartLastBar.time) {
        // নতুন ক্যান্ডেল শুরু হলো (যেমন নতুন ১৫ সেকেন্ড বা নতুন ১ মিনিট শুরু হওয়া মাত্র)
        activeChartLastBar = {
            time: bucketTime,
            open: livePrice,
            high: livePrice,
            low: livePrice,
            close: livePrice
        };
    } else {
        // চলমান লাইভ ক্যান্ডেল প্রতি টিকে টিকে আপডেট হচ্ছে
        activeChartLastBar = {
            time: activeChartLastBar.time,
            open: activeChartLastBar.open,
            high: Math.max(activeChartLastBar.high, livePrice),
            low: Math.min(activeChartLastBar.low, livePrice),
            close: livePrice
        };
    }

    try {
        arafCandleSeries.update(activeChartLastBar);
    } catch (e) {}

    const ltpEl = document.getElementById("chartLiveLtpBadge");
    if (ltpEl) ltpEl.textContent = activeChartIsCrypto ? `$${livePrice.toLocaleString()}` : `₹${livePrice.toFixed(2)}`;
}

async function initTradingViewChart(symbolName, yahooOverride = "") {
    currentActiveSymbol = symbolName || "NSE:NIFTY";
    activeChartYahooSymbol = yahooOverride || "";

    const titleEl = document.getElementById("tvChartCurrentTitle");
    const badgeEl = document.getElementById("chartSourceBadge");
    const ltpEl = document.getElementById("chartLiveLtpBadge");
    const frame = document.getElementById("tvLiveChartFrame");
    if (!frame) return;

    if (titleEl) titleEl.textContent = currentActiveSymbol;
    frame.innerHTML = `<div class="h-full flex items-center justify-center text-xs font-mono text-slate-500"><i class="fa-solid fa-spinner animate-spin text-[#00d09c] mr-2"></i> Loading Live Candles...</div>`;

    const rawSym = currentActiveSymbol.includes(":") ? currentActiveSymbol.split(":")[1] : currentActiveSymbol;
    const isCrypto = currentActiveSymbol.startsWith("BINANCE:") || rawSym.endsWith("USDT") || rawSym.endsWith("-USD");
    activeChartIsCrypto = isCrypto;

    let candles = [];
    let sourceLabel = "IN-APP LIVE CHART";

    try {
        if (isCrypto) {
            const pair = rawSym.replace("-USD", "USDT").replace(/[^A-Z0-9]/gi, "").toUpperCase();
            if (SESSION_VAULT.coindcxLinked && auth.currentUser) {
                const apiTf = activeChartTimeframe === "15s" ? "1m" : activeChartTimeframe;
                const cRes = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(pair)}&timeframe=${apiTf}&range=5d`
                });
                if (cRes.ok && cRes.data?.success && Array.isArray(cRes.data.candles)) {
                    candles = cRes.data.candles.map(c => ({ time: Math.floor(c.time / 1000), open: c.open, high: c.high, low: c.low, close: c.close }));
                    sourceLabel = cRes.data.source || "COINDCX DIRECT";
                }
            } else if (activeChartTimeframe === "15s") {
                const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=${encodeURIComponent(pair)}&interval=1s&limit=900`);
                if (r.ok) {
                    const raw = await r.json();
                    const buckets = new Map();
                    (raw || []).forEach(k => {
                        const bTime = Math.floor(Math.floor(+k[0] / 1000) / 15) * 15;
                        const prev = buckets.get(bTime);
                        if (!prev) buckets.set(bTime, { time: bTime, open: +k[1], high: +k[2], low: +k[3], close: +k[4] });
                        else { prev.high = Math.max(prev.high, +k[2]); prev.low = Math.min(prev.low, +k[3]); prev.close = +k[4]; }
                    });
                    candles = Array.from(buckets.values()).sort((a, b) => a.time - b.time);
                    sourceLabel = "BINANCE 15S LIVE";
                }
            } else {
                const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=${encodeURIComponent(pair)}&interval=${activeChartTimeframe}&limit=100`);
                if (r.ok) {
                    const raw = await r.json();
                    candles = (raw || []).map(k => ({ time: Math.floor(+k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4] }));
                    sourceLabel = `BINANCE ${activeChartTimeframe.toUpperCase()} LIVE`;
                }
            }
        } else {
            const apiTf = activeChartTimeframe === "15s" ? "1m" : activeChartTimeframe;
            const rangeMap = { "1m": "1d", "5m": "5d", "15m": "5d", "1h": "1mo", "1d": "1mo" };
            let j = null;
            if (SESSION_VAULT.shoonyaLinked && auth.currentUser) {
                const sRes = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(rawSym)}&yahooSymbol=${encodeURIComponent(activeChartYahooSymbol)}&timeframe=${apiTf}&range=${rangeMap[apiTf] || "5d"}`
                });
                j = sRes.data;
            } else {
                const r = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(rawSym)}&yahooSymbol=${encodeURIComponent(activeChartYahooSymbol)}&timeframe=${apiTf}&range=${rangeMap[apiTf] || "5d"}`
                });
                j = r.ok ? r.data : null;
            }
            if (j?.success && Array.isArray(j.candles)) {
                if (activeChartTimeframe === "15s") {
                    const subCandles = [];
                    j.candles.forEach(c => {
                        const baseSec = Math.floor(c.time / 1000) + 19800;
                        const spread = (c.close - c.open) / 4;
                        subCandles.push({ time: baseSec, open: c.open, high: Math.max(c.open, c.open + spread), low: Math.min(c.open, c.open + spread), close: Number((c.open + spread).toFixed(2)) });
                        subCandles.push({ time: baseSec + 15, open: Number((c.open + spread).toFixed(2)), high: c.high, low: Math.min(c.open + spread, c.low), close: Number(((c.open + c.close) / 2).toFixed(2)) });
                        subCandles.push({ time: baseSec + 30, open: Number(((c.open + c.close) / 2).toFixed(2)), high: Math.max(c.high, (c.open + c.close) / 2), low: c.low, close: Number((c.close - spread).toFixed(2)) });
                        subCandles.push({ time: baseSec + 45, open: Number((c.close - spread).toFixed(2)), high: Math.max(c.close, c.high), low: Math.min(c.close - spread, c.low), close: c.close });
                    });
                    candles = subCandles.slice(-120);
                    sourceLabel = "15S LIVE TICKS (INTERPOLATED)";
                } else {
                    candles = j.candles.map(c => ({ time: Math.floor(c.time / 1000) + 19800, open: c.open, high: c.high, low: c.low, close: c.close }));
                    sourceLabel = j.source || "MARKET_FEED";
                }
            }
        }
    } catch (e) {}

    if (badgeEl) badgeEl.textContent = sourceLabel;
    if (!candles.length || !window.LightweightCharts) {
        frame.innerHTML = `<div class="h-full flex flex-col items-center justify-center text-xs font-mono text-slate-500 p-4 text-center"><i class="fa-solid fa-chart-area text-2xl text-slate-300 mb-2"></i><span>No candle data found for ${sanitizeCleanText(currentActiveSymbol)}.</span></div>`;
        return;
    }

    frame.innerHTML = "";
    if (arafChartInstance) {
        try { arafChartInstance.remove(); } catch (e) {}
        arafChartInstance = null;
    }

    arafChartInstance = LightweightCharts.createChart(frame, {
        width: frame.clientWidth || 360,
        height: 410,
        layout: { background: { color: "#ffffff" }, textColor: "#334155" },
        grid: { vertLines: { color: "#f1f5f9" }, horzLines: { color: "#f1f5f9" } },
        timeScale: { timeVisible: true, secondsVisible: activeChartTimeframe === "15s", borderColor: "#e2e8f0" },
        rightPriceScale: { borderColor: "#e2e8f0" }
    });

    arafCandleSeries = arafChartInstance.addCandlestickSeries({
        upColor: "#00d09c", downColor: "#eb5b3c",
        borderUpColor: "#00d09c", borderDownColor: "#eb5b3c",
        wickUpColor: "#00d09c", wickDownColor: "#eb5b3c"
    });
    arafCandleSeries.setData(candles);
    activeChartLastBar = { ...candles[candles.length - 1] };

    const calcEmaLine = (period) => {
        if (candles.length < period) return [];
        const k = 2 / (period + 1);
        let ema = candles.slice(0, period).reduce((s, b) => s + b.close, 0) / period;
        const out = [{ time: candles[period - 1].time, value: ema }];
        for (let i = period; i < candles.length; i++) {
            ema = candles[i].close * k + ema * (1 - k);
            out.push({ time: candles[i].time, value: Number(ema.toFixed(4)) });
        }
        return out;
    };

    const ema9Series = arafChartInstance.addLineSeries({ color: "#00d09c", lineWidth: 1.5, title: "EMA9" });
    ema9Series.setData(calcEmaLine(9));
    const ema21Series = arafChartInstance.addLineSeries({ color: "#f59e0b", lineWidth: 1.5, title: "EMA21" });
    ema21Series.setData(calcEmaLine(21));

    const lastBar = candles[candles.length - 1];
    const highs = candles.map(c => c.high), lows = candles.map(c => c.low);
    const sr = calculateDynamicSupportResistance(Math.max(...highs), Math.min(...lows), lastBar.close);

    arafCandleSeries.createPriceLine({ price: Number(sr.r1), color: "#eb5b3c", lineWidth: 1, lineStyle: 2, title: "R1" });
    arafCandleSeries.createPriceLine({ price: Number(sr.s1), color: "#00d09c", lineWidth: 1, lineStyle: 2, title: "S1" });

    if (ltpEl) ltpEl.textContent = isCrypto ? `$${lastBar.close}` : `₹${lastBar.close.toFixed(2)}`;
    const legendEl = document.getElementById("chartOhlcLegend");
    if (legendEl) legendEl.textContent = `EMA 9/21 • S1: ${sr.s1} • R1: ${sr.r1}`;

    arafChartInstance.timeScale().fitContent();
}

function calculateDynamicSupportResistance(high, low, close) {
    if (!Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close) || high <= 0 || low <= 0 || close <= 0) {
        return { pivot: "0.0", r1: "0.0", s1: "0.0" };
    }
    const P = (high + low + close) / 3;
    const R1 = (2 * P) - low;
    const S1 = (2 * P) - high;
    return { pivot: P.toFixed(2), r1: R1.toFixed(2), s1: S1.toFixed(2) };
}

function aggregateHigherTimeframes(assetId) {
    const bars15m = QUANT_FEATURE_STORE.candlesByTf[assetId]?.["15m"] || [];
    if (!bars15m.length) return;
    ["30m", "1h", "4h", "1d"].forEach(tf => {
        const tfMs = TF_MS[tf];
        const buckets = new Map();
        bars15m.forEach(b => {
            const start = Math.floor(b.time / tfMs) * tfMs;
            const prev = buckets.get(start);
            if (!prev) {
                buckets.set(start, { time: start, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume || 0, isClosed: true });
            } else {
                prev.high = Math.max(prev.high, b.high);
                prev.low = Math.min(prev.low, b.low);
                prev.close = b.close;
                prev.volume += (b.volume || 0);
            }
        });
        QUANT_FEATURE_STORE.candlesByTf[assetId][tf] = Array.from(buckets.values()).sort((a, b) => a.time - b.time);
    });
}

async function loadRealHistoricalCandles(assetId) {
    if (!SESSION_VAULT.localCandleHistory[assetId]) SESSION_VAULT.localCandleHistory[assetId] = [];
    const asset = MASTER_ASSETS.find(a => a.id === assetId);
    if (!asset) return;

        try {
        let bars = [];
        if (asset.category === "crypto" || asset.id === "goldmcx") {
            const sym = asset.id === "goldmcx" ? "PAXGUSDT" : asset.orderKey;
            if (SESSION_VAULT.coindcxLinked && auth.currentUser) {
                const cRes = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(sym)}&timeframe=15m&range=5d`
                });
                if (cRes.ok && cRes.data?.success && Array.isArray(cRes.data.candles)) {
                    bars = cRes.data.candles.map(c => ({ ...c, isClosed: true, source: cRes.data.source || "COINDCX_DIRECT" }));
                }
            } else {
                const res = await fetch(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=15m&limit=200`);
                if (!res.ok) return;
                const raw = await res.json();
                if (Array.isArray(raw)) {
                    bars = raw.map(k => ({
                        time: Number(k[0]), open: Number(k[1]), high: Number(k[2]),
                        low: Number(k[3]), close: Number(k[4]), volume: Number(k[5] || 0),
                        isClosed: true, source: "BINANCE_SPOT_KLINES"
                    })).filter(b => Number.isFinite(b.close) && b.close > 0 && b.high >= b.low);
                }
            }
        } else {
            let data = null;
            if (SESSION_VAULT.shoonyaLinked && auth.currentUser) {
                const sRes = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(asset.orderKey)}&timeframe=15m&range=5d`
                });
                data = sRes.data;
            } else {
                const res = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(asset.orderKey)}&timeframe=15m&range=5d`
                });
                if (res.ok) data = res.data;
            }
            if (data && Array.isArray(data.candles)) {
                bars = data.candles.map(c => ({ ...c, isClosed: true, source: data.source || "YAHOO_DELAYED" }));
            }
        }
        if (bars.length > 0) {
            SESSION_VAULT.localCandleHistory[assetId] = bars;
            if (!QUANT_FEATURE_STORE.candlesByTf[assetId]) {
                QUANT_FEATURE_STORE.candlesByTf[assetId] = { "1m": [], "3m": [], "5m": [], "15m": [], "30m": [], "1h": [], "4h": [], "1d": [] };
            }
            QUANT_FEATURE_STORE.candlesByTf[assetId]["15m"] = bars;
            aggregateHigherTimeframes(assetId);
        }
    } catch (e) {
        console.warn(`[Candle History Error - ${assetId}]:`, e.message);
    }
}

function ingestNormalizedTick(envelope) {
    if (!envelope || !envelope.assetId) return;
    const price = Number(envelope.last);
    if (!Number.isFinite(price) || price <= 0) return;

    const item = MASTER_ASSETS.find(a => a.id === envelope.assetId);
    if (!item) return;

    const eventTime = Number(envelope.eventTime) || Date.now();
    if (item.dataEnvelope?.eventTime && eventTime < item.dataEnvelope.eventTime) return;

    const anomaly = updateQuantCandleStore(item.id, price, Number(envelope.volumeDelta || 0), eventTime);
    if (anomaly?.isAnomaly) return;

    const now = Date.now();
    const latencyMs = Math.max(0, now - eventTime);
    const validQualities = ["LIVE", "DELAYED", "STALE", "OFFLINE", "INVALID"];
    const quality = validQualities.includes(envelope.quality) ? envelope.quality : "LIVE";

    item.dataEnvelope = {
        source: envelope.source || "UNKNOWN",
        exchange: envelope.exchange || "",
        marketType: envelope.marketType || item.dataEnvelope?.marketType || "SPOT",
        segment: envelope.segment || "",
        symbol: envelope.symbol || item.orderKey,
        eventTime,
        receivedAt: now,
        latencyMs,
        quality
    };

    const todayIst = getIndiaTradingDate();
    if (item.sessionDate !== todayIst) {
        item.sessionDate = todayIst;
        item.high = Number.isFinite(envelope.high) && envelope.high > 0 ? envelope.high : price;
        item.low = Number.isFinite(envelope.low) && envelope.low > 0 ? envelope.low : price;
    } else {
        if (Number.isFinite(envelope.high) && envelope.high > item.high) item.high = envelope.high;
        if (Number.isFinite(envelope.low) && (item.low === 0 || envelope.low < item.low)) item.low = envelope.low;
        if (price > item.high || item.high === 0) item.high = price;
        if (price < item.low || item.low === 0) item.low = price;
    }

    if (Number.isFinite(envelope.tbq)) item.realTbq = envelope.tbq;
    if (Number.isFinite(envelope.tsq)) item.realTsq = envelope.tsq;
    if (item.realTbq > 0 || item.realTsq > 0) {
        const ratio = item.realTbq / (item.realTbq + item.realTsq);
        item.blockOrderDetected = ratio >= 0.72 || ratio <= 0.28;
    }

    const isCrypto = item.category === "crypto" || item.id === "goldmcx";
    const formatted = isCrypto
        ? price.toLocaleString("en-US", { style: "currency", currency: "USD" })
        : `₹${price.toFixed(2)}`;

    updateAssetPrice(item.id, formatted, price, envelope.change || item.change || "0.00%");
    updateAssetFreshnessBadge(item.id, item.dataEnvelope.quality, item.dataEnvelope.source, latencyMs);
    evaluateMillisecondMtmOnTick(item.orderKey, price);
    updateLiveInAppChartCandle(item.orderKey, price, eventTime);

    if (currentSelectedAsset && currentSelectedAsset.id === item.id) {
        if (item.id === "nifty50") computeRealBlackScholes(price);
        if (now - lastQuantUiTime > 300) {
            lastQuantUiTime = now;
            runQuantOrchestrator(item.id);
        }
    }
}

function updateAssetFreshnessBadge(assetId, quality, source, latencyMs = 0) {
    const badge = document.getElementById(`oppBadge_${assetId}`);
    if (!badge) return;
    const qColors = {
        LIVE:    "bg-[#00d09c]/15 text-[#059669] border-[#00d09c]/40",
        DELAYED: "bg-amber-500/15 text-amber-600 border-amber-500/40",
        STALE:   "bg-orange-500/15 text-orange-600 border-orange-500/40",
        OFFLINE: "bg-[#eb5b3c]/15 text-[#eb5b3c] border-[#eb5b3c]/40",
        INVALID: "bg-[#eb5b3c]/15 text-[#eb5b3c] border-[#eb5b3c]/40"
    };
    badge.dataset.feedQuality = quality;
    badge.title = `Source: ${source} | Latency: ${latencyMs}ms`;
    if (quality === "STALE" || quality === "OFFLINE" || quality === "DELAYED" || quality === "INVALID") {
        badge.textContent = `${quality} • ${source}`;
        badge.className = `text-[8px] font-mono font-bold px-1.5 py-0.5 rounded border ${qColors[quality] || qColors.OFFLINE}`;
    }
}

function checkFeedFreshnessWatchdog() {
    const now = Date.now();
    MASTER_ASSETS.forEach(item => {
        const env = item.dataEnvelope;
        if (!env || !env.receivedAt) {
            updateAssetFreshnessBadge(item.id, "OFFLINE", "NO_FEED");
            return;
        }
        const ageMs = now - env.receivedAt;
        if (ageMs > 60000) {
            env.quality = "OFFLINE";
            updateAssetFreshnessBadge(item.id, "OFFLINE", env.source, ageMs);
        } else if (ageMs > 15000 && env.quality === "LIVE") {
            env.quality = "STALE";
            updateAssetFreshnessBadge(item.id, "STALE", env.source, ageMs);
        }
    });
}

function updateAssetPrice(assetId, formatted, num, change) {
    const item = MASTER_ASSETS.find(a => a.id === assetId);
    let isUp = true;
    if (item) {
        isUp = num >= (item.priceNum || num);
        item.price = formatted;
        item.priceNum = num;
        item.change = change;
        item.highFormatted = (item.category === "crypto" || item.id === "goldmcx") ? `$${item.high.toFixed(2)}` : `₹${item.high.toFixed(2)}`;
        item.lowFormatted = (item.category === "crypto" || item.id === "goldmcx") ? `$${item.low.toFixed(2)}` : `₹${item.low.toFixed(2)}`;
        const chgNum = parseFloat(change) || 0;
        item.trend = chgNum > 0.15 ? "BULLISH" : (chgNum < -0.15 ? "BEARISH" : "NEUTRAL");
        const mLabel = item.dataEnvelope?.marketType === "SPOT_REF" ? "SPOT_REF" : (item.dataEnvelope?.quality || "LIVE");
        item.signalType = `${mLabel} • ${item.dataEnvelope?.source || "FEED"}`;
    }

    const pEl = document.getElementById(`priceCardVal_${assetId}`);
    const cEl = document.getElementById(`priceCardChg_${assetId}`);
    if (pEl) {
        pEl.textContent = formatted;
        pEl.classList.remove("price-flash-up", "price-flash-down");
        void pEl.offsetWidth;
        pEl.classList.add(isUp ? "price-flash-up" : "price-flash-down");
    }
    if (cEl) {
        cEl.textContent = change;
        cEl.className = change.startsWith("+")
            ? "text-[10px] font-mono font-bold text-[#00d09c] bg-[#00d09c]/10 px-2 py-0.5 rounded-md border border-[#00d09c]/25"
            : "text-[10px] font-mono font-bold text-[#eb5b3c] bg-[#eb5b3c]/10 px-2 py-0.5 rounded-md border border-[#eb5b3c]/25";
    }
}

function updateTickerTape(valId, chgId, formattedPrice, change) {
    const elP = document.getElementById(valId);
    const elC = document.getElementById(chgId);
    if (elP) elP.textContent = formattedPrice;
    if (elC) {
        elC.textContent = change;
        elC.className = change.startsWith("+") ? "text-[#00d09c] ml-1 text-[9px] font-bold" : "text-[#eb5b3c] ml-1 text-[9px] font-bold";
    }
}

function connectBinanceWs() {
    if (binanceWs && (binanceWs.readyState === WebSocket.OPEN || binanceWs.readyState === WebSocket.CONNECTING)) return;
    try {
        const streams = [
            "btcusdt@ticker", "ethusdt@ticker", "paxgusdt@ticker",
            "btcusdt@depth20@100ms", "ethusdt@depth20@100ms", "paxgusdt@depth20@100ms"
        ].join("/");
        binanceWs = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`);
        binanceWs.onopen = () => { binanceReconnectAttempts = 0; };
        binanceWs.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (!msg || !msg.data) return;

                if (msg.stream && msg.stream.includes("@depth20")) {
                    const sym = msg.stream.split("@")[0].toUpperCase();
                    const bids = msg.data.bids || [];
                    const asks = msg.data.asks || [];
                    if (bids.length > 0 && asks.length > 0) {
                        const bestBid = Number(bids[0][0]);
                        const bestAsk = Number(asks[0][0]);
                        const bidNotional = bids.reduce((a, l) => {
                            const p = Number(l?.[0]), q = Number(l?.[1]);
                            return (Number.isFinite(p) && Number.isFinite(q) && p > 0 && q > 0) ? a + (p * q) : a;
                        }, 0);
                        const askNotional = asks.reduce((a, l) => {
                            const p = Number(l?.[0]), q = Number(l?.[1]);
                            return (Number.isFinite(p) && Number.isFinite(q) && p > 0 && q > 0) ? a + (p * q) : a;
                        }, 0);
                        const total = bidNotional + askNotional;
                        if (Number.isFinite(total) && total > 0 && bestBid > 0 && bestAsk >= bestBid) {
                            const bPct = Math.round((bidNotional / total) * 100);
                            LIVE_L2_DEPTH_MAP[sym] = {
                                buyers: bPct, sellers: 100 - bPct,
                                isBlock: bPct >= 75 || bPct <= 25,
                                status: "LIVE", method: "BINANCE_SPOT_L2_100MS",
                                marketType: "SPOT_REF",
                                bestBid, bestAsk, depthNotional: total, hasL2: true, updatedAt: Date.now()
                            };
                        }
                    }
                    return;
                }

                const d = msg.data;
                if (!d || !d.c) return;
                const priceVal = Number(d.c);
                if (!Number.isFinite(priceVal) || priceVal <= 0) return;

                const priceFormatted = priceVal.toLocaleString("en-US", { style: "currency", currency: "USD" });
                const changePercent = `${parseFloat(d.P) >= 0 ? "+" : ""}${parseFloat(d.P).toFixed(2)}%`;
                const common = {
                    source: "BINANCE_SPOT_WS", exchange: "BINANCE_SPOT", marketType: "SPOT_REF", segment: "CRYPTO_SPOT_REF",
                    last: priceVal, high: Number(d.h || priceVal), low: Number(d.l || priceVal),
                    change: changePercent, eventTime: Number(d.E || Date.now()), quality: "LIVE"
                };

                if (d.s === "BTCUSDT") {
                    const el = document.getElementById("tickerBtc"); if (el) el.textContent = priceFormatted;
                    ingestNormalizedTick({ ...common, assetId: "btcusdt", symbol: "BTCUSDT" });
                }
                if (d.s === "ETHUSDT") {
                    const el = document.getElementById("tickerEth"); if (el) el.textContent = priceFormatted;
                    ingestNormalizedTick({ ...common, assetId: "ethusdt", symbol: "ETHUSDT" });
                }
                if (d.s === "PAXGUSDT") {
                    const el = document.getElementById("tickerGold"); if (el) el.textContent = priceFormatted;
                    ingestNormalizedTick({ ...common, assetId: "goldmcx", symbol: "PAXGUSDT" });
                }
            } catch (err) {
                console.warn("[Binance WS Parse]:", err.message);
            }
        };
        binanceWs.onclose = () => {
            binanceReconnectAttempts++;
            const delay = Math.min(30000, 1500 * Math.pow(2, binanceReconnectAttempts)) + Math.floor(Math.random() * 1000);
            binanceReconnectTimer = setTimeout(connectBinanceWs, delay);
        };
        binanceWs.onerror = () => binanceWs?.close();
    } catch (e) {
        console.warn("[Binance WS Init]:", e.message);
    }
}

function connectBackendPriceWs() {
    if (backendPriceWs && (backendPriceWs.readyState === WebSocket.OPEN || backendPriceWs.readyState === WebSocket.CONNECTING)) return;
    const symbols = [
        "BTCUSDT", "ETHUSDT", "PAXGUSDT",
        "NIFTY", "BANKNIFTY", "SENSEX", "BANKEX",
        "RELIANCE", "HDFCBANK", "TATAMOTORS", "TCS", "INFY"
    ].join(",");
    const wsUrl = `${FASTAPI_BASE_URL.replace("http", "ws")}/feeds/ws/prices?symbols=${encodeURIComponent(symbols)}`;
    try {
        backendPriceWs = new WebSocket(wsUrl);
        backendPriceWs.onopen = () => {
            backendPriceWsReconnectAttempts = 0;
            console.log("[Backend Price WS] Connected");
        };
        backendPriceWs.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (!msg || !msg.type) return;
                if (msg.type === "snapshot" && msg.data) {
                    Object.entries(msg.data).forEach(([sym, data]) => {
                        if (data) ingestNormalizedTick({ ...data, symbol: sym });
                    });
                } else if (msg.type === "tick" && msg.data) {
                    ingestNormalizedTick(msg.data);
                }
            } catch (err) {
                console.warn("[Backend Price WS Parse]:", err.message);
            }
        };
        backendPriceWs.onclose = () => {
            backendPriceWsReconnectAttempts++;
            const delay = Math.min(30000, 1500 * Math.pow(2, backendPriceWsReconnectAttempts)) + Math.floor(Math.random() * 1000);
            backendPriceWsReconnectTimer = setTimeout(connectBackendPriceWs, delay);
            console.log(`[Backend Price WS] Reconnecting in ${delay}ms (attempt ${backendPriceWsReconnectAttempts})`);
        };
        backendPriceWs.onerror = () => backendPriceWs?.close();
    } catch (e) {
        console.warn("[Backend Price WS Init]:", e.message);
    }
}

async function syncCoinDcxOrBinanceFeed() {
    if (SESSION_VAULT.coindcxLinked && auth.currentUser && isTerminalUnlocked) {
        // CoinDCX linked - fetch depth data for L2
        const cRes = await secureApiCall("coindcx_market_feed", { method: "GET" });
        if (cRes.ok && cRes.data?.success && cRes.data.coindcxConnected && cRes.data.quotes) {
            window.LAST_COINDCX_ALL_USDT = Array.isArray(cRes.data.allUsdt) ? cRes.data.allUsdt : [];
            const q = cRes.data.quotes;
            const cMap = [
                { id: "btcusdt", sym: "BTCUSDT", tickerEl: "tickerBtc", data: q.btcusdt },
                { id: "ethusdt", sym: "ETHUSDT", tickerEl: "tickerEth", data: q.ethusdt },
                { id: "goldmcx", sym: "PAXGUSDT", tickerEl: "tickerGold", data: q.goldmcx }
            ];
            cMap.forEach(m => {
                if (m.data?.price > 0) {
                    if (m.data.depth) {
                        LIVE_L2_DEPTH_MAP[m.sym] = {
                            buyers: m.data.depth.buyers,
                            sellers: m.data.depth.sellers,
                            isBlock: m.data.depth.buyers >= 75 || m.data.depth.buyers <= 25,
                            status: "LIVE",
                            method: "COINDCX_ORDERBOOK",
                            source: "COINDCX_L2",
                            marketType: "CRYPTO_FUTURES",
                            bestBid: m.data.depth.bestBid,
                            bestAsk: m.data.depth.bestAsk,
                            depthNotional: m.data.depth.depthNotional,
                            hasL2: true,
                            updatedAt: Date.now()
                        };
                    }
                    const priceFmt = m.data.price.toLocaleString("en-US", { style: "currency", currency: "USD" });
                    const tkEl = document.getElementById(m.tickerEl);
                    if (tkEl) tkEl.textContent = priceFmt;
                    ingestNormalizedTick({
                        assetId: m.id,
                        source: "COINDCX_DIRECT",
                        exchange: "COINDCX",
                        marketType: "CRYPTO_FUTURES",
                        segment: "CRYPTO_FUTURES",
                        symbol: m.sym,
                        last: m.data.price,
                        high: m.data.high,
                        low: m.data.low,
                        change: m.data.change,
                        eventTime: m.data.event_time || Date.now(),
                        quality: "LIVE"
                    });
                }
            });
            updateAutoTradeRouteBadge();
            if (!currentSelectedAsset) {
                const masterFirst = MASTER_ASSETS.find(a => a.orderKey === "NIFTY");
                if (masterFirst) selectAssetForAnalysis(masterFirst.symbol, masterFirst.orderKey);
            }
            return;
        }
    }
    // CoinDCX not linked - prices come from backend Price WS (Binance Spot WS)
    // Only Binance depth WS is maintained separately
    updateAutoTradeRouteBadge();
}

async function fetchMasterOrPublicIndianFeed() {
    if (SESSION_VAULT.shoonyaLinked && auth.currentUser && isTerminalUnlocked) {
        const mRes = await secureApiCall("master_market_feed", { method: "GET" });
        if (mRes.ok && mRes.data?.success && mRes.data.quotes) {
            const q = mRes.data.quotes;
            if (q.vix?.price) {
                liveIndiaVix = q.vix.price;
                liveVixMeta = { value: q.vix.price, source: "SHOONYA_MASTER", updatedAt: Date.now(), quality: "LIVE" };
                const vixEl = document.getElementById("tickerVix");
                if (vixEl) vixEl.textContent = liveIndiaVix.toFixed(2);
            }
            const map = [
                { id: "nifty50", key: "nifty50", sym: "NIFTY", exch: "NSE", tVal: "tickerNifty", tChg: "tickerNiftyChange" },
                { id: "banknifty", key: "banknifty", sym: "BANKNIFTY", exch: "NSE", tVal: "tickerBankNifty", tChg: "tickerBankNiftyChange" },
                { id: "sensex", key: "sensex", sym: "SENSEX", exch: "BSE", tVal: "tickerSensex", tChg: "tickerSensexChange" },
                { id: "bankex", key: "bankex", sym: "BANKEX", exch: "BSE" },
                { id: "reliance", key: "reliance", sym: "RELIANCE", exch: "NSE" },
                { id: "hdfcbank", key: "hdfcbank", sym: "HDFCBANK", exch: "NSE" },
                { id: "tatamotors", key: "tatamotors", sym: "TATAMOTORS", exch: "NSE" },
                { id: "tcs_bse", key: "tcs_bse", sym: "TCS", exch: "BSE" },
                { id: "infy_bse", key: "infy_bse", sym: "INFY", exch: "BSE" }
            ];
            map.forEach(m => {
                const item = q[m.key];
                if (item?.price) {
                    ingestNormalizedTick({
                        assetId: m.id, source: "SHOONYA_DIRECT", exchange: m.exch, marketType: "INDEX_EQ", segment: "INDEX_EQ",
                        symbol: m.sym, last: item.price, high: item.high, low: item.low,
                        change: item.change, tbq: item.tbq, tsq: item.tsq,
                        eventTime: item.event_time, quality: "LIVE"
                    });
                    if (m.tVal) updateTickerTape(m.tVal, m.tChg, `₹${item.price.toFixed(2)}`, item.change);
                }
            });
            updateAutoTradeRouteBadge();
            return;
        }
    }

    // Shoonya NOT connected -> fetch Indian market data from Yahoo Finance
    try {
        const res = await secureApiCall("public_market_feed", { method: "GET" });
        if (!res.ok) return;
        const data = res.data;
        if (data && data.success) {
            if (data.vix?.price) {
                liveIndiaVix = data.vix.price;
                liveVixMeta = { value: data.vix.price, source: "YAHOO_PROXY", updatedAt: Date.now(), quality: "DELAYED" };
                const vixEl = document.getElementById("tickerVix");
                if (vixEl) vixEl.textContent = `${liveIndiaVix.toFixed(2)} (DLY)`;
            }
            const pairs = [
                { id: "nifty50", q: data.nifty, sym: "NIFTY", exch: "NSE", tVal: "tickerNifty", tChg: "tickerNiftyChange" },
                { id: "banknifty", q: data.banknifty, sym: "BANKNIFTY", exch: "NSE", tVal: "tickerBankNifty", tChg: "tickerBankNiftyChange" },
                { id: "sensex", q: data.sensex, sym: "SENSEX", exch: "BSE", tVal: "tickerSensex", tChg: "tickerSensexChange" },
                { id: "reliance", q: data.reliance, sym: "RELIANCE", exch: "NSE" },
                { id: "hdfcbank", q: data.hdfcbank, sym: "HDFCBANK", exch: "NSE" },
                { id: "tatamotors", q: data.tatamotors, sym: "TATAMOTORS", exch: "NSE" },
                { id: "tcs_bse", q: data.tcs_bse, sym: "TCS", exch: "BSE" },
                { id: "infy_bse", q: data.infy_bse, sym: "INFY", exch: "BSE" }
            ];
            pairs.forEach(p => {
                if (p.q?.price) {
                    ingestNormalizedTick({
                        assetId: p.id, source: "YAHOO_FINANCE_PROXY", exchange: p.exch, marketType: "DELAYED", segment: "DELAYED",
                        symbol: p.sym, last: p.q.price, high: p.q.high, low: p.q.low,
                        change: p.q.change, eventTime: p.q.event_time, quality: "DELAYED"
                    });
                    if (p.tVal) updateTickerTape(p.tVal, p.tChg, `₹${p.q.price.toFixed(2)}`, p.q.change);
                }
            });
            updateAutoTradeRouteBadge();
            if (!currentSelectedAsset) {
                const masterFirst = MASTER_ASSETS.find(a => a.orderKey === "NIFTY");
                if (masterFirst) selectAssetForAnalysis(masterFirst.symbol, masterFirst.orderKey);
            }
        }
    } catch (e) {
        console.warn("[Public Feed]:", e.message);
    }
}

const QUANT_FEATURE_STORE = {
    candlesByTf: {},
    cvdMap: {},
    anomalyLog: [],
    executionLog: [],
    attributionByRegime: {},
    adaptiveScorecard: { autoLiveMutationAllowed: false, totalRecorded: 0, rejectRatePct: 0 }
};

const TF_MS = { "15s": 15000, "1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000, "4h": 14400000, "1d": 86400000 };

function detectMarketAnomaly(assetId, tickPrice, tickVolume = 0, existingCandles = []) {
    if (!Number.isFinite(tickPrice) || tickPrice <= 0) return { isAnomaly: true, type: "INVALID_PRICE" };
    if (existingCandles.length >= 10) {
        const lastClose = existingCandles[existingCandles.length - 1].close;
        const pctJump = Math.abs((tickPrice - lastClose) / lastClose);
        if (pctJump > 0.10) {
            QUANT_FEATURE_STORE.anomalyLog.unshift({ assetId, type: "EXTREME_TICK_SPIKE", time: Date.now() });
            QUANT_FEATURE_STORE.anomalyLog = QUANT_FEATURE_STORE.anomalyLog.slice(0, 50);
            return { isAnomaly: true, type: "EXTREME_TICK_SPIKE" };
        }
    }
    if (Number.isFinite(tickVolume) && tickVolume < 0) return { isAnomaly: true, type: "NEGATIVE_VOLUME" };
    return { isAnomaly: false };
}

function updateQuantCandleStore(assetId, price, volumeDelta = 0, eventTimeMs = Date.now()) {
    if (!QUANT_FEATURE_STORE.candlesByTf[assetId]) {
        QUANT_FEATURE_STORE.candlesByTf[assetId] = { "15s": [], "1m": [], "3m": [], "5m": [], "15m": [], "30m": [], "1h": [], "4h": [], "1d": [] };
    }
    const base15m = QUANT_FEATURE_STORE.candlesByTf[assetId]["15m"];
    const anomaly = detectMarketAnomaly(assetId, price, volumeDelta, base15m);
    if (anomaly.isAnomaly) return anomaly;

    for (const [tf, ms] of Object.entries(TF_MS)) {
        const list = QUANT_FEATURE_STORE.candlesByTf[assetId][tf];
        const bucketStart = Math.floor(eventTimeMs / ms) * ms;
        const last = list[list.length - 1];
        if (!last || last.time < bucketStart) {
            if (last) last.isClosed = true;
            list.push({ time: bucketStart, open: price, high: price, low: price, close: price, volume: Math.max(0, volumeDelta), isClosed: false });
            if (list.length > 200) list.shift();
        } else if (last.time === bucketStart) {
            last.high = Math.max(last.high, price);
            last.low = Math.min(last.low, price);
            last.close = price;
            last.volume += Math.max(0, volumeDelta);
        }
    }
    return { isAnomaly: false };
}

function getClosedBars(assetId, tf = "15m") {
    const raw = QUANT_FEATURE_STORE.candlesByTf[assetId]?.[tf] || [];
    return raw.filter((c, idx) => c.isClosed === true || idx < raw.length - 1);
}

function computeEMA(values, period) {
    if (!Array.isArray(values) || values.length < period) return null;
    const k = 2 / (period + 1);
    let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
    for (let i = period; i < values.length; i++) {
        ema = (values[i] * k) + (ema * (1 - k));
    }
    return Number(ema.toFixed(2));
}

function computeRSI(closes, period = 14) {
    if (!Array.isArray(closes) || closes.length <= period) return null;
    let gains = 0, losses = 0;
    for (let i = 1; i <= period; i++) {
        const diff = closes[i] - closes[i - 1];
        if (diff >= 0) gains += diff;
        else losses += Math.abs(diff);
    }
    let avgGain = gains / period;
    let avgLoss = losses / period;

    for (let i = period + 1; i < closes.length; i++) {
        const diff = closes[i] - closes[i - 1];
        const g = diff > 0 ? diff : 0;
        const l = diff < 0 ? Math.abs(diff) : 0;
        avgGain = ((avgGain * (period - 1)) + g) / period;
        avgLoss = ((avgLoss * (period - 1)) + l) / period;
    }
    if (avgLoss === 0) return 100;
    const rs = avgGain / avgLoss;
    return Number((100 - (100 / (1 + rs))).toFixed(2));
}

async function fetchRealOrderflowDelta(asset) {
    const quality = asset?.dataEnvelope?.quality || "OFFLINE";
    if (!asset || asset.priceNum === 0 || quality === "OFFLINE" || quality === "STALE" || quality === "INVALID") {
        return { buyers: 0, sellers: 0, isBlock: false, status: quality === "STALE" ? "STALE" : "UNAVAILABLE", method: "NONE", marketType: "NONE", bestBid: null, bestAsk: null, hasL2: false };
    }
    if (asset.category === "crypto" || asset.id === "goldmcx") {
        const sym = asset.id === "goldmcx" ? "PAXGUSDT" : asset.orderKey;
        const cached = LIVE_L2_DEPTH_MAP[sym];
        if (cached && (Date.now() - cached.updatedAt < 15000)) {
            return cached;
        }
        return { buyers: 0, sellers: 0, isBlock: false, status: "UNAVAILABLE", method: "WAITING_SPOT_WS_DEPTH", marketType: "SPOT_REF", bestBid: null, bestAsk: null, hasL2: false };
    }
    if (Number.isFinite(asset.realTbq) && Number.isFinite(asset.realTsq) && (asset.realTbq > 0 || asset.realTsq > 0)) {
        const total = asset.realTbq + asset.realTsq;
        const bPct = Math.round((asset.realTbq / total) * 100);
        return { buyers: bPct, sellers: 100 - bPct, isBlock: asset.blockOrderDetected, status: "LIVE", method: "SHOONYA_TBQ_TSQ", marketType: "INDEX_EQ", bestBid: null, bestAsk: null, hasL2: false };
    }
    return { buyers: 0, sellers: 0, isBlock: false, status: "UNAVAILABLE", method: "NONE", marketType: "NONE", bestBid: null, bestAsk: null, hasL2: false };
}

function computeSmartOrderFlow(assetId, depthFlow) {
    if (!depthFlow || depthFlow.status !== "LIVE") return { status: "UNAVAILABLE", buyersPct: null, sellersPct: null, flowShift: false };
    if (!QUANT_FEATURE_STORE.cvdMap[assetId]) QUANT_FEATURE_STORE.cvdMap[assetId] = { cumulativeDelta: 0, history: [] };
    const netDeltaPct = depthFlow.buyers - depthFlow.sellers;
    const state = QUANT_FEATURE_STORE.cvdMap[assetId];
    state.cumulativeDelta += netDeltaPct;
    state.history.push({ time: Date.now(), buyers: depthFlow.buyers });
    if (state.history.length > 30) state.history.shift();
    const flowShift = state.history.length >= 3 && Math.abs(depthFlow.buyers - state.history[0].buyers) >= 15;
    return { status: "LIVE", buyersPct: depthFlow.buyers, sellersPct: depthFlow.sellers, cumulativeDeltaScore: state.cumulativeDelta, flowShift, marketType: depthFlow.marketType, label: "ESTIMATED_OB_IMBALANCE" };
}

function computeLiquidityIntelligence(bestBid, bestAsk, depthNotional = null, hasL2 = false) {
    if (!Number.isFinite(bestBid) || !Number.isFinite(bestAsk) || bestBid <= 0 || bestAsk <= 0) {
        return { status: "UNAVAILABLE", spreadPct: null, thinBook: null, scope: "NO_QUOTE" };
    }
    const mid = (bestBid + bestAsk) / 2;
    const spreadPct = ((bestAsk - bestBid) / mid) * 100;
    return { status: "LIVE", spreadPct: Number(spreadPct.toFixed(4)), thinBook: spreadPct > 0.15, depthNotional, scope: hasL2 ? "SPOT_L2_DEPTH" : "TOP_OF_BOOK_ONLY" };
}

function computeVolumeIntelligence(candles, asset) {
    if (!Array.isArray(candles) || candles.length < 10) return { status: "UNAVAILABLE", vwap: null, rvol: null };
    const isCrypto = asset?.category === "crypto" || asset?.id === "goldmcx";
    const now = new Date();
    const sessionStartMs = isCrypto
        ? Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 0, 0)
        : new Date(`${getIndiaTradingDate()}T09:15:00+05:30`).getTime();

    const sessionBars = candles.filter(c => c.time >= sessionStartMs && c.volume > 0);
    if (sessionBars.length < 3) return { status: "INCOMPLETE_SESSION", vwap: null, rvol: null };
    let sumTpV = 0, sumV = 0;
    sessionBars.forEach(b => { sumTpV += ((b.high + b.low + b.close) / 3) * b.volume; sumV += b.volume; });
    const vwap = sumV > 0 ? sumTpV / sumV : null;
    const baselineBars = candles.slice(-21, -1).filter(c => c.volume > 0);
    const avgVol = baselineBars.length > 0 ? baselineBars.reduce((s, b) => s + b.volume, 0) / baselineBars.length : 0;
    const rvol = avgVol > 0 ? candles[candles.length - 1].volume / avgVol : null;
    return { status: "LIVE", vwap: vwap ? Number(vwap.toFixed(2)) : null, rvol: rvol ? Number(rvol.toFixed(2)) : null };
}

function computeVolatilityEngine(candles, assetCategory = "indian", lookback = 14) {
    if (!Array.isArray(candles) || candles.length < lookback + 5) {
        return { atr_value: null, realized_volatility: null, volatility_state: "UNAVAILABLE", data_quality: "INSUFFICIENT_BARS" };
    }
    const trList = [], logReturns = [];
    for (let i = 1; i < candles.length; i++) {
        const c = candles[i], prev = candles[i - 1];
        const tr = Math.max(c.high - c.low, Math.abs(c.high - prev.close), Math.abs(c.low - prev.close));
        if (Number.isFinite(tr)) trList.push(tr);
        if (c.close > 0 && prev.close > 0) logReturns.push(Math.log(c.close / prev.close));
    }
    const recentTr = trList.slice(-lookback);
    const atr = recentTr.length === lookback ? recentTr.reduce((a, b) => a + b, 0) / lookback : null;
    const baseTr = trList.slice(-(lookback * 3), -lookback);
    const baseAtr = baseTr.length > 0 ? baseTr.reduce((a, b) => a + b, 0) / baseTr.length : atr;
    let volState = "NORMAL";
    if (atr && baseAtr) {
        if (atr > baseAtr * 1.25) volState = "EXPANSION";
        else if (atr < baseAtr * 0.80) volState = "CONTRACTION";
    }
    let realizedVol = null;
    if (logReturns.length >= 10) {
        const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
        const variance = logReturns.reduce((s, r) => s + Math.pow(r - mean, 2), 0) / (logReturns.length - 1);
        const barsPerYear = assetCategory === "crypto" || assetCategory === "commodity" ? 365 * 96 : 252 * 25;
        realizedVol = Number((Math.sqrt(variance * barsPerYear) * 100).toFixed(2));
    }
    return { atr_value: atr ? Number(atr.toFixed(2)) : null, realized_volatility: realizedVol, volatility_state: volState, data_quality: "LIVE" };
}

function detectMarketRegime(candles, volEngine, volIntel) {
    if (!Array.isArray(candles) || candles.length < 20 || volEngine.data_quality !== "LIVE") {
        return { regime: "UNKNOWN", confidence: 0, strategyAllowlist: [] };
    }
    const closes = candles.slice(-20).map(c => c.close);
    const emaFast = closes.slice(-5).reduce((a, b) => a + b, 0) / 5;
    const emaSlow = closes.reduce((a, b) => a + b, 0) / 20;
    const slopePct = ((emaFast - emaSlow) / emaSlow) * 100;
    if (volEngine.volatility_state === "EXPANSION" && Math.abs(slopePct) < 0.15) return { regime: "HIGH_VOL_CHOP", confidence: 72, strategyAllowlist: ["MEAN_REVERSION"] };
    if (slopePct >= 0.20) return { regime: "TRENDING_BULL", confidence: volIntel?.rvol > 1.2 ? 82 : 68, strategyAllowlist: ["MOMENTUM_LONG"] };
    if (slopePct <= -0.20) return { regime: "TRENDING_BEAR", confidence: volIntel?.rvol > 1.2 ? 82 : 68, strategyAllowlist: ["MOMENTUM_SHORT"] };
    return { regime: "RANGING", confidence: 65, strategyAllowlist: ["RANGE_S1_R1"] };
}

function analyzeMultiTimeframeAlignment(assetId) {
    const tf15 = getClosedBars(assetId, "15m"), tf1h = getClosedBars(assetId, "1h");
    const bias = (bars) => bars.length < 5 ? "UNAVAILABLE" : (bars[bars.length - 1].close > bars[bars.length - 5].close * 1.001 ? "BULLISH" : (bars[bars.length - 1].close < bars[bars.length - 5].close * 0.999 ? "BEARISH" : "NEUTRAL"));
    const b15 = bias(tf15), b1h = bias(tf1h);
    return { tf15m: b15, tf1h: b1h, aligned: b15 !== "UNAVAILABLE" && b1h !== "UNAVAILABLE" && b15 === b1h && b15 !== "NEUTRAL" };
}

const CRYPTO_DERIVATIVES_CACHE = {};

async function fetchCryptoDerivativesSnapshot(orderKey) {
    const sym = String(orderKey || "").toUpperCase();
    if (!sym.endsWith("USDT")) return null;
    const cached = CRYPTO_DERIVATIVES_CACHE[sym];
    if (cached && (Date.now() - cached.updatedAt < 60000)) return cached;

    try {
        const [premRes, oiRes] = await Promise.allSettled([
            fetch(`https://fapi.binance.com/fapi/v1/premiumIndex?symbol=${encodeURIComponent(sym)}`),
            fetch(`https://fapi.binance.com/fapi/v1/openInterest?symbol=${encodeURIComponent(sym)}`)
        ]);
        const prem = premRes.status === "fulfilled" && premRes.value.ok ? await premRes.value.json().catch(() => null) : null;
        const oi = oiRes.status === "fulfilled" && oiRes.value.ok ? await oiRes.value.json().catch(() => null) : null;

        if (!prem && !oi) return { status: "UNAVAILABLE", fundingRatePct: null, openInterest: null, source: "BINANCE_FAPI", updatedAt: Date.now() };
        const snap = {
            status: "LIVE",
            fundingRatePct: prem?.lastFundingRate !== undefined ? Number((Number(prem.lastFundingRate) * 100).toFixed(4)) : null,
            markPrice: prem?.markPrice ? Number(prem.markPrice) : null,
            openInterest: oi?.openInterest ? Number(Number(oi.openInterest).toFixed(2)) : null,
            source: "BINANCE_FUTURES_PUBLIC",
            updatedAt: Date.now()
        };
        CRYPTO_DERIVATIVES_CACHE[sym] = snap;
        return snap;
    } catch (e) {
        return { status: "UNAVAILABLE", fundingRatePct: null, openInterest: null, source: "BINANCE_FAPI", updatedAt: Date.now() };
    }
}

function analyzeOptionsIntelligence(verifiedChain = null) {
    if (!verifiedChain || !Number.isFinite(verifiedChain.callOi)) return { status: "UNAVAILABLE", oiPcr: null };
    return { status: "LIVE", oiPcr: Number((verifiedChain.putOi / verifiedChain.callOi).toFixed(2)) };
}

function computeCorrelationEngine(targetAssetId, peerAssetId = "btcusdt", windowBars = 20) {
    if (targetAssetId === peerAssetId) peerAssetId = "ethusdt";
    const aBars = getClosedBars(targetAssetId, "15m");
    const bBars = getClosedBars(peerAssetId, "15m");
    if (aBars.length < 12 || bBars.length < 12) return { status: "UNAVAILABLE", pearson: null, samples: 0, peer: peerAssetId.toUpperCase() };

    const bMap = new Map(bBars.map((b, i) => [b.time, i > 0 ? Math.log(b.close / bBars[i - 1].close) : null]));
    const xs = [], ys = [];
    for (let i = 1; i < aBars.length; i++) {
        const retB = bMap.get(aBars[i].time);
        if (retB !== undefined && retB !== null && aBars[i].close > 0 && aBars[i - 1].close > 0) {
            xs.push(Math.log(aBars[i].close / aBars[i - 1].close));
            ys.push(retB);
        }
    }
    const n = Math.min(windowBars, xs.length);
    if (n < 8) return { status: "UNSYNCED", pearson: null, samples: n, peer: peerAssetId.toUpperCase() };

    const sx = xs.slice(-n), sy = ys.slice(-n);
    const mx = sx.reduce((a, b) => a + b, 0) / n, my = sy.reduce((a, b) => a + b, 0) / n;
    let num = 0, dx = 0, dy = 0;
    for (let i = 0; i < n; i++) {
        num += (sx[i] - mx) * (sy[i] - my);
        dx += Math.pow(sx[i] - mx, 2);
        dy += Math.pow(sy[i] - my, 2);
    }
    const denom = Math.sqrt(dx * dy);
    return { status: "LIVE", pearson: denom > 0 ? Number((num / denom).toFixed(3)) : 0, samples: n, peer: peerAssetId.toUpperCase(), note: "Correlation != causation" };
}

function detectFalseBreakout(candles, resLevel, supLevel, orderFlow) {
    if (!Array.isArray(candles) || candles.length < 3 || !resLevel || !supLevel) return { detected: false };
    const last = candles[candles.length - 1];
    if (last.high > resLevel && last.close < resLevel && orderFlow?.sellersPct > 55) return { detected: true, type: "HEURISTIC_BULL_TRAP" };
    if (last.low < supLevel && last.close > supLevel && orderFlow?.buyersPct > 55) return { detected: true, type: "HEURISTIC_BEAR_TRAP" };
    return { detected: false };
}

function detectLiquiditySweep(candles, liquidityIntel, lookback = 10) {
    if (!Array.isArray(candles) || candles.length < lookback + 2) return { detected: false };
    const prior = candles.slice(-(lookback + 1), -1);
    const sH = Math.max(...prior.map(c => c.high)), sL = Math.min(...prior.map(c => c.low));
    const last = candles[candles.length - 1];
    if (last.high > sH && last.close < sH) return { detected: true, side: "BUY_SIDE_SWEEP", evidence: liquidityIntel?.scope === "SPOT_L2_DEPTH" ? "SPOT_L2_DEPTH" : "WICK_PROXY" };
    if (last.low < sL && last.close > sL) return { detected: true, side: "SELL_SIDE_SWEEP", evidence: liquidityIntel?.scope === "SPOT_L2_DEPTH" ? "SPOT_L2_DEPTH" : "WICK_PROXY" };
    return { detected: false };
}

function predictExpectedSlippage(liquidityIntel, volEngine, latencyMs = 100) {
    if (liquidityIntel.status !== "LIVE" || volEngine.data_quality !== "LIVE") return { status: "UNAVAILABLE", estimatedSlippageBps: null };
    const halfSpreadBps = (liquidityIntel.spreadPct * 100) / 2;
    const latBps = Math.min(10, latencyMs / 250);
    const volBps = volEngine.volatility_state === "EXPANSION" ? 4.5 : 1.5;
    return { status: "ESTIMATED", estimatedSlippageBps: Number((halfSpreadBps + latBps + volBps).toFixed(2)) };
}

function evaluateNewsEventRiskFilter(asset) {
    if (newsFetchState !== "LOADED" || !allFetchedNewsItems.length) {
        return { status: "UNKNOWN", state: "UNKNOWN", riskLevel: "UNKNOWN", count: 0, rumorCount: 0, reason: null };
    }
    const now = Date.now();
    const cutoff6h = now - 6 * 3600 * 1000;
    const cutoff2h = now - 2 * 3600 * 1000;

    const hits = allFetchedNewsItems.filter(n => (Date.parse(n.pubDate) || 0) >= cutoff6h && (isGeopoliticalOrMacro(n.title) || isNewsRelatedToAsset(n.title, asset)));
    const verifiedHits = hits.filter(n => n.trustTier !== "LOW_TRUST_RUMOR");
    const rumorCount = hits.length - verifiedHits.length;

    const severeHalt = verifiedHits.find(n =>
        (Date.parse(n.pubDate) || 0) >= cutoff2h &&
        (n.eventCategory === "CRITICAL_EVENT" || /(trading halt|circuit breaker|market crash|emergency sanction|all-out war)/i.test(n.title))
    );
    if (severeHalt) {
        return { status: "LIVE", state: "HALT_NO_TRADE", riskLevel: "CRITICAL_HALT", count: verifiedHits.length, rumorCount, reason: severeHalt.title };
    }

    const hasBull = verifiedHits.some(n => n.impact === "BULLISH");
    const hasBear = verifiedHits.some(n => n.impact === "BEARISH");
    if (hasBull && hasBear) {
        return { status: "LIVE", state: "SOURCE_CONFLICT", riskLevel: "ELEVATED", count: verifiedHits.length, rumorCount, reason: "Conflicting bullish & bearish catalysts" };
    }

    const hasOfficial = verifiedHits.some(n => n.trustTier === "OFFICIAL");
    const state = verifiedHits.length > 0 ? (hasOfficial ? "CONFIRMED" : "PRE_EVENT") : "NORMAL";
    return {
        status: "LIVE",
        state,
        riskLevel: verifiedHits.length > 0 ? "ELEVATED" : (rumorCount > 0 ? "RUMOR_ONLY" : "NORMAL"),
        count: verifiedHits.length,
        rumorCount,
        reason: verifiedHits[0]?.title || null
    };
}

function recordExecutionQualityMetric({ symbol, regime = "UNKNOWN", arrivalPrice, fillPrice, status }) {
    const shortfallBps = (arrivalPrice > 0 && fillPrice > 0)
        ? Number((((fillPrice - arrivalPrice) / arrivalPrice) * 10000).toFixed(2))
        : null;
    QUANT_FEATURE_STORE.executionLog.unshift({ symbol, regime, arrivalPrice, fillPrice, shortfallBps, status, ts: Date.now() });
    QUANT_FEATURE_STORE.executionLog = QUANT_FEATURE_STORE.executionLog.slice(0, 100);

    if (!QUANT_FEATURE_STORE.attributionByRegime[regime]) {
        QUANT_FEATURE_STORE.attributionByRegime[regime] = { orders: 0, filled: 0, rejected: 0 };
    }
    QUANT_FEATURE_STORE.attributionByRegime[regime].orders += 1;
    if (status === "FILLED" || status === "PAPER_FILLED") QUANT_FEATURE_STORE.attributionByRegime[regime].filled += 1;
    if (status === "REJECTED") QUANT_FEATURE_STORE.attributionByRegime[regime].rejected += 1;

    const tot = QUANT_FEATURE_STORE.executionLog.length;
    const rej = QUANT_FEATURE_STORE.executionLog.filter(x => x.status === "REJECTED").length;
    QUANT_FEATURE_STORE.adaptiveScorecard = {
        autoLiveMutationAllowed: false,
        totalRecorded: tot,
        rejectRatePct: Number(((rej / tot) * 100).toFixed(1))
    };
}

function runWalkForwardValidation(assetId) {
    const asset = MASTER_ASSETS.find(a => a.id === assetId);
    const bars = getClosedBars(assetId, "15m").filter(b => b && b.isClosed !== false);
    if (bars.length < 40) {
        return {
            status: "INSUFFICIENT_DATA", modelVersion: "E55_WF_BUYSELL_v2.0", horizonBars: 8,
            trades: 0, buyTrades: 0, sellTrades: 0, ambiguousBars: 0,
            winRatePct: null, calibratedProbPct: null, ciLowerPct: null, ciUpperPct: null,
            brierScore: null, netReturnPct: null, maxDrawdownPct: null, profitFactor: null,
            expectancyPct: null, overfittingWarning: true, calibratedDate: getIndiaTradingDate()
        };
    }

    const isCrypto = asset?.category === "crypto" || asset?.category === "commodity";
    const roundTripCostPct = isCrypto ? 0.18 : 0.14;
    const horizonBars = 8;
    const splitIdx = Math.floor(bars.length * 0.55);

    let equity = 100, peak = 100, maxDd = 0, grossWin = 0, grossLoss = 0;
    let wins = 0, trades = 0, buyTrades = 0, sellTrades = 0, ambiguousBars = 0, netPct = 0, brierSum = 0;

    let i = Math.max(22, splitIdx);
    while (i < bars.length - 2) {
        const windowCloses = bars.slice(0, i + 1).map(b => b.close);
        const ema9 = computeEMA(windowCloses, 9);
        const ema21 = computeEMA(windowCloses, 21);

        let trSum = 0;
        for (let k = i - 13; k <= i; k++) {
            trSum += Math.max(
                bars[k].high - bars[k].low,
                Math.abs(bars[k].high - bars[k - 1].close),
                Math.abs(bars[k].low - bars[k - 1].close)
            );
        }
        const atr = trSum / 14;
        const entryBar = bars[i];
        const entry = entryBar.close;

        let side = null;
        if (ema9 && ema21 && atr > 0) {
            if (ema9 > ema21 && entry > ema9) side = "BUY";
            else if (ema9 < ema21 && entry < ema9) side = "SELL";
        }

        if (!side) { i++; continue; }

        const slDist = atr * 1.5;
        const tpDist = atr * 2.25;
        const sl = side === "BUY" ? entry - slDist : entry + slDist;
        const tp = side === "BUY" ? entry + tpDist : entry - tpDist;

        let exitBarIdx = Math.min(i + horizonBars, bars.length - 1);
        let outcome = 0;
        let grossRetPct = 0;
        let resolved = false;

        for (let j = i + 1; j <= Math.min(i + horizonBars, bars.length - 1); j++) {
            const b = bars[j];
            const hitTp = side === "BUY" ? (b.high >= tp) : (b.low <= tp);
            const hitSl = side === "BUY" ? (b.low <= sl) : (b.high >= sl);

            if (hitTp && hitSl) {
                ambiguousBars++;
                outcome = 0;
                grossRetPct = -(slDist / entry) * 100;
                exitBarIdx = j;
                resolved = true;
                break;
            }
            if (hitSl) {
                outcome = 0;
                grossRetPct = -(slDist / entry) * 100;
                exitBarIdx = j;
                resolved = true;
                break;
            }
            if (hitTp) {
                outcome = 1;
                grossRetPct = (tpDist / entry) * 100;
                exitBarIdx = j;
                resolved = true;
                break;
            }
        }

        if (!resolved) {
            const lastB = bars[exitBarIdx];
            grossRetPct = side === "BUY"
                ? ((lastB.close - entry) / entry) * 100
                : ((entry - lastB.close) / entry) * 100;
            outcome = (grossRetPct - roundTripCostPct) > 0 ? 1 : 0;
        }

        const netRetPct = grossRetPct - roundTripCostPct;
        const priorCalProb = (wins + 5) / (trades + 10);
        brierSum += Math.pow(priorCalProb - outcome, 2);

        trades++;
        if (side === "BUY") buyTrades++;
        else sellTrades++;
        if (outcome === 1) wins++;

        netPct += netRetPct;
        equity *= (1 + netRetPct / 100);
        if (equity > peak) peak = equity;
        const dd = ((peak - equity) / peak) * 100;
        if (dd > maxDd) maxDd = dd;

        if (netRetPct > 0) grossWin += netRetPct;
        else grossLoss += Math.abs(netRetPct);

        i = exitBarIdx + 1;
    }

    if (trades === 0) {
        return {
            status: "NO_SIGNALS", modelVersion: "E55_WF_BUYSELL_v2.0", horizonBars,
            trades: 0, buyTrades: 0, sellTrades: 0, ambiguousBars: 0,
            winRatePct: null, calibratedProbPct: null, ciLowerPct: null, ciUpperPct: null,
            brierScore: null, netReturnPct: null, maxDrawdownPct: null, profitFactor: null,
            expectancyPct: null, overfittingWarning: true, calibratedDate: getIndiaTradingDate()
        };
    }

    const pHat = wins / trades;
    const z = 1.96;
    const denom = 1 + (z * z) / trades;
    const center = (pHat + (z * z) / (2 * trades)) / denom;
    const half = (z * Math.sqrt((pHat * (1 - pHat) + (z * z) / (4 * trades)) / trades)) / denom;
    const ciLowerPct = Number((Math.max(0, center - half) * 100).toFixed(1));
    const ciUpperPct = Number((Math.min(1, center + half) * 100).toFixed(1));

    const shrunkProbPct = Number((((wins + 5) / (trades + 10)) * 100).toFixed(1));
    const hasSufficientSample = trades >= 20;
    const status = hasSufficientSample ? "COMPLETE" : "INSUFFICIENT_SAMPLE";
    const pf = grossLoss > 0 ? Number((grossWin / grossLoss).toFixed(2)) : (grossWin > 0 ? 99 : 0);

    return {
        status,
        modelVersion: "E55_WF_BUYSELL_v2.0",
        horizonBars,
        trades,
        buyTrades,
        sellTrades,
        ambiguousBars,
        winRatePct: Number((pHat * 100).toFixed(1)),
        calibratedProbPct: hasSufficientSample ? shrunkProbPct : null,
        ciLowerPct,
        ciUpperPct,
        brierScore: Number((brierSum / trades).toFixed(3)),
        netReturnPct: Number(netPct.toFixed(2)),
        maxDrawdownPct: Number(maxDd.toFixed(2)),
        profitFactor: pf,
        expectancyPct: Number((netPct / trades).toFixed(3)),
        overfittingWarning: !hasSufficientSample,
        calibratedDate: getIndiaTradingDate()
    };

}

function evaluateTwoWayIntradaySetup(asset, quantSnap, depthFlow, sr) {
    const feedQual = asset?.dataEnvelope?.quality || "OFFLINE";
    const canonical = resolveCanonicalInstrument(asset?.orderKey);
    const ltp = asset?.priceNum || 0;

    if (!asset || ltp <= 0 || feedQual === "OFFLINE" || feedQual === "STALE" || feedQual === "INVALID") {
        return {
            direction: "NO TRADE",
            optionHint: "WAIT",
            status: "WAIT",
            score: 0,
            rrRatio: "--",
            entry: "--",
            sl: "--",
            target: "--",
            reasons: ["Feed offline or stale — trading paused"],
            invalidation: "Wait for fresh live/delayed market tick"
        };
    }

    if (quantSnap?.eventRisk?.state === "HALT_NO_TRADE") {
        return {
            direction: "NO TRADE",
            optionHint: "HALT",
            status: "HALT_NO_TRADE",
            score: 0,
            rrRatio: "--",
            entry: ltp.toFixed(2),
            sl: "--",
            target: "--",
            reasons: [`E01 Event Halt: ${quantSnap.eventRisk.reason || "Severe Macro/Exchange Event"}`],
            invalidation: "Trading paused until official event stabilizes"
        };
    }

    const ema9 = quantSnap?.ema9;
    const ema21 = quantSnap?.ema21;
    const rsi14 = quantSnap?.rsi14;
    const vwap = quantSnap?.volIntel?.vwap;
    const atr = quantSnap?.volatility?.atr_value || (ltp * 0.004);
    const reg = quantSnap?.regime?.regime || "UNKNOWN";
    const fb = quantSnap?.falseBreakout;
    const sweep = quantSnap?.sweep;
    const liquidity = quantSnap?.liquidity;

    if (liquidity?.thinBook === true) {
        return {
            direction: "NO TRADE",
            optionHint: "WAIT",
            status: "NO TRADE",
            score: 20,
            rrRatio: "--",
            entry: ltp.toFixed(2),
            sl: "--",
            target: "--",
            reasons: [`Spread too wide (${liquidity.spreadPct}%) — thin orderbook`],
            invalidation: "Wait for liquidity normalization"
        };
    }

    let bullScore = 0;
    let bearScore = 0;
    const bullReasons = [];
    const bearReasons = [];

    if (ema9 !== null && ema21 !== null) {
        if (ema9 > ema21 && ltp > ema9) { bullScore += 25; bullReasons.push(`EMA9 (${ema9}) > EMA21 (${ema21})`); }
        else if (ema9 < ema21 && ltp < ema9) { bearScore += 25; bearReasons.push(`EMA9 (${ema9}) < EMA21 (${ema21})`); }
    }

    if (rsi14 !== null) {
        if (rsi14 >= 55 && rsi14 <= 72) { bullScore += 15; bullReasons.push(`RSI ${rsi14} Bullish Momentum`); }
        else if (rsi14 <= 45 && rsi14 >= 28) { bearScore += 15; bearReasons.push(`RSI ${rsi14} Bearish Momentum`); }
    }

    if (vwap !== null) {
        if (ltp > vwap) { bullScore += 15; bullReasons.push(`Above VWAP (${vwap})`); }
        else if (ltp < vwap) { bearScore += 15; bearReasons.push(`Below VWAP (${vwap})`); }
    }

    if (reg === "TRENDING_BULL") { bullScore += 15; bullReasons.push("Regime: Bullish Trend"); }
    else if (reg === "TRENDING_BEAR") { bearScore += 15; bearReasons.push("Regime: Bearish Trend"); }

    if (depthFlow?.status === "LIVE") {
        if (depthFlow.buyers >= 62) { bullScore += 20; bullReasons.push(`Orderbook Bid Skew (${depthFlow.buyers}%)`); }
        else if (depthFlow.sellers >= 62) { bearScore += 20; bearReasons.push(`Orderbook Ask Skew (${depthFlow.sellers}%)`); }
    }

    if (fb?.detected) {
        if (fb.type === "HEURISTIC_BEAR_TRAP") { bullScore += 15; bullReasons.push("Bear Trap Reversal Footprint"); }
        if (fb.type === "HEURISTIC_BULL_TRAP") { bearScore += 15; bearReasons.push("Bull Trap Reversal Footprint"); }
    }
    if (sweep?.detected) {
        if (sweep.side === "SELL_SIDE_SWEEP") { bullScore += 10; bullReasons.push("Sell-Side Liquidity Sweep"); }
        if (sweep.side === "BUY_SIDE_SWEEP") { bearScore += 10; bearReasons.push("Buy-Side Liquidity Sweep"); }
    }

    const activePos = SESSION_VAULT.fastPositionsMap[asset.orderKey];
    const hasLongHolding = activePos && activePos.qty > 0;
    const isShortAllowed = canonical?.supportsShort !== false;

    const minThreshold = 60;
    let direction = "NO TRADE";
    let optionHint = "WAIT";
    let status = "WAIT FOR CONFIRMATION";
    let activeReasons = ["Mixed/conflicting intraday signals"];
    let entry = ltp;
    let sl = ltp;
    let target = ltp;
    let invalidation = "Setup incomplete";
    let score = Math.max(bullScore, bearScore);

    if (bullScore >= minThreshold && bullScore >= bearScore + 15) {
        direction = "BUY";
        optionHint = asset.category === "indian" ? "CALL / LONG" : "LONG";
        status = "ACTIVE";
        activeReasons = bullReasons;
        sl = Math.min(Number(sr.s1) || (ltp - atr * 1.5), ltp - atr * 1.5);
        target = Math.max(Number(sr.r1) || (ltp + atr * 2.5), ltp + atr * 2.5);
        invalidation = `Close below ${sl.toFixed(2)} or EMA9 crossing under EMA21`;
    } else if (bearScore >= minThreshold && bearScore >= bullScore + 15) {
        direction = hasLongHolding ? "SELL (EXIT LONG)" : (isShortAllowed ? "SHORT" : "NO TRADE");
        optionHint = asset.category === "indian" ? "PUT / SHORT" : "SHORT";
        status = direction === "NO TRADE" ? "NO TRADE" : "ACTIVE";
        activeReasons = bearReasons;
        sl = Math.max(Number(sr.r1) || (ltp + atr * 1.5), ltp + atr * 1.5);
        target = Math.min(Number(sr.s1) || (ltp - atr * 2.5), ltp - atr * 2.5);
        invalidation = `Close above ${sl.toFixed(2)} or EMA9 crossing above EMA21`;
    }

    const riskDist = Math.abs(entry - sl);
    const rewardDist = Math.abs(target - entry);
    const rrNum = riskDist > 0 ? rewardDist / riskDist : 0;

    if (direction !== "NO TRADE" && rrNum < 1.3) {
        return {
            direction: "NO TRADE",
            optionHint: "WAIT",
            status: "NO TRADE",
            score,
            rrRatio: `1:${rrNum.toFixed(1)}`,
            entry: entry.toFixed(2),
            sl: sl.toFixed(2),
            target: target.toFixed(2),
            reasons: ["Poor Risk/Reward ratio (< 1:1.3)"],
            invalidation: "Wait for pullback to improve R:R"
        };
    }

    return {
        direction,
        optionHint,
        status,
        score: Math.min(95, score),
        rrRatio: rrNum > 0 ? `1:${rrNum.toFixed(1)}` : "--",
        entry: entry.toFixed(2),
        sl: sl.toFixed(2),
        target: target.toFixed(2),
        reasons: activeReasons,
        invalidation
    };
}

async function runQuantOrchestrator(assetId) {
    const asset = MASTER_ASSETS.find(a => a.id === assetId);
    if (!asset) return null;
    const env = asset.dataEnvelope || { quality: "OFFLINE", latencyMs: 0 };
    const bars15m = getClosedBars(asset.id, "15m");
    const closes15m = bars15m.map(b => b.close);

    const ema9 = computeEMA(closes15m, 9);
    const ema21 = computeEMA(closes15m, 21);
    const rsi14 = computeRSI(closes15m, 14);

    const depthFlow = await fetchRealOrderflowDelta(asset);
    const orderFlow = computeSmartOrderFlow(asset.id, depthFlow);
    const liquidity = computeLiquidityIntelligence(depthFlow.bestBid, depthFlow.bestAsk, depthFlow.depthNotional, depthFlow.hasL2);
    const volIntel = computeVolumeIntelligence(bars15m, asset);
    const volatility = computeVolatilityEngine(bars15m, asset.category, 14);
    const regime = detectMarketRegime(bars15m, volatility, volIntel);
    const mtf = analyzeMultiTimeframeAlignment(asset.id);
    const optionsIntel = analyzeOptionsIntelligence(null);
    const cryptoDeriv = (asset.category === "crypto" || asset.id === "goldmcx") ? await fetchCryptoDerivativesSnapshot(asset.orderKey) : null;
    const peerId = (asset.category === "indian") ? "nifty50" : "btcusdt";
    const corr = computeCorrelationEngine(asset.id, asset.id === "nifty50" ? "btcusdt" : peerId, 20);
    const sr = calculateDynamicSupportResistance(asset.high, asset.low, asset.priceNum);
    const falseBreakout = detectFalseBreakout(bars15m, Number(sr.r1), Number(sr.s1), orderFlow);
    const sweep = detectLiquiditySweep(bars15m, liquidity);
    const slippageEst = predictExpectedSlippage(liquidity, volatility, env.latencyMs);
    const eventRisk = evaluateNewsEventRiskFilter(asset);
    const walkFwd = runWalkForwardValidation(asset.id);

    const quantSnap = { ema9, ema21, rsi14, orderFlow, liquidity, volIntel, volatility, regime, mtf, optionsIntel, cryptoDeriv, corr, falseBreakout, sweep, slippageEst, eventRisk, walkFwd };
    const twoWay = evaluateTwoWayIntradaySetup(asset, quantSnap, depthFlow, sr);
    quantSnap.twoWay = twoWay;

    const qBadge = document.getElementById("quantDataQualityBadge");
    if (qBadge) qBadge.textContent = `${env.quality} • ${env.source || "NO_FEED"}`;
    const elReg = document.getElementById("qMetricRegime");
    if (elReg) elReg.textContent = `${regime.regime} (${regime.confidence}%)`;
    const elVol = document.getElementById("qMetricVol");
    if (elVol) elVol.textContent = volatility.atr_value ? `ATR ${volatility.atr_value} / ${volatility.realized_volatility ?? "--"}%` : "UNAVAILABLE";
    const elVwap = document.getElementById("qMetricVwap");
    if (elVwap) elVwap.textContent = volIntel.vwap ? `${volIntel.vwap} (${volIntel.rvol ?? "--"}x)` : volIntel.status;

    const elSig = document.getElementById("qMetricSignal");
    if (elSig) {
        elSig.textContent = `${twoWay.direction} (${twoWay.rrRatio})`;
        elSig.className = twoWay.direction.includes("BUY")
            ? "text-[#00d09c] font-black"
            : (twoWay.direction.includes("SHORT") || twoWay.direction.includes("SELL") || twoWay.status === "HALT_NO_TRADE" ? "text-[#eb5b3c] font-black" : "text-amber-600 font-bold");
    }

    const elEmaRsi = document.getElementById("qMetricEmaRsi");
    if (elEmaRsi) {
        elEmaRsi.textContent = (ema9 !== null && rsi14 !== null) ? `${ema9}/${ema21} • RSI ${rsi14}` : "UNAVAILABLE";
    }

    const elFlags = document.getElementById("qMetricFlags");
    if (elFlags) {
        const flags = [];
        if (mtf.aligned) flags.push(`MTF:${mtf.tf15m}`);
        if (falseBreakout.detected) flags.push(falseBreakout.type);
        if (sweep.detected) flags.push(`${sweep.side}(${sweep.evidence})`);
        if (cryptoDeriv?.status === "LIVE") flags.push(`Fund:${cryptoDeriv.fundingRatePct}% OI:${cryptoDeriv.openInterest}`);
        else flags.push(`Opt:${optionsIntel.status}`);
        flags.push(`Slip:${slippageEst.estimatedSlippageBps ?? "--"}bps`);
        elFlags.textContent = flags.join(" • ");
    }
    const elAdv = document.getElementById("qMetricAdvanced");
    if (elAdv) {
        const corrTxt = corr.status === "LIVE" ? `${corr.pearson} (${corr.peer})` : corr.status;
        const calTxt = walkFwd.calibratedProbPct !== null
            ? `CalProb: ${walkFwd.calibratedProbPct}% [${walkFwd.ciLowerPct}-${walkFwd.ciUpperPct}%] Brier:${walkFwd.brierScore} (${walkFwd.buyTrades}B/${walkFwd.sellTrades}S)`
            : `${walkFwd.status} (${walkFwd.trades || 0}T)`;
        elAdv.textContent = `Corr: ${corrTxt} • Event: ${eventRisk.riskLevel} • ${calTxt}`;
    }

    if (typeof runAdditiveEnginesPipeline === "function") {
        const fusionRep = runAdditiveEnginesPipeline(asset, twoWay, quantSnap);
        quantSnap.fusionReport = fusionRep;
        if (fusionRep && typeof renderNewEnginesCard === "function") {
            renderNewEnginesCard(fusionRep);
        }
    }
    return quantSnap;
}
function evaluateUltraOpportunity(asset, flow, sr, quantSnap = null) {
    const badge = document.getElementById(`oppBadge_${asset.id}`);
    const card = document.getElementById(`assetCard_${asset.id}`);
    const feedQual = asset?.dataEnvelope?.quality || "OFFLINE";

    if (!asset || asset.priceNum === 0 || flow.status !== "LIVE" || feedQual === "OFFLINE" || feedQual === "STALE" || feedQual === "INVALID") {
        if (badge && card) {
            card.classList.remove("radar-blink-buy", "radar-blink-sell");
            badge.textContent = `${feedQual} • WAIT`;
            badge.className = "text-[8px] font-mono font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded border border-slate-200";
        }
        return { score: 0, isConfirmed: false, side: "NO TRADE", reasons: ["Waiting for fresh orderbook feed"] };
    }

    const bars15m = getClosedBars(asset.id, "15m");
    const closes = bars15m.map(b => b.close);
    const ema9 = quantSnap?.ema9 ?? computeEMA(closes, 9);
    const ema21 = quantSnap?.ema21 ?? computeEMA(closes, 21);
    const rsi14 = quantSnap?.rsi14 ?? computeRSI(closes, 14);

    const side = flow.buyers >= flow.sellers ? "BUY" : "SHORT";
    const dominantPressure = side === "BUY" ? flow.buyers : flow.sellers;
    let score = 35;
    const reasons = [];

    if (dominantPressure >= 65) {
        score += 25;
        reasons.push(`${flow.marketType === "SPOT_REF" ? "Binance Spot Depth" : "Orderbook Imbalance"} (${dominantPressure}%)`);
    }
    if (side === "BUY" && ema9 && ema21 && ema9 > ema21) { score += 15; reasons.push(`EMA9>EMA21`); }
    if (side === "SHORT" && ema9 && ema21 && ema9 < ema21) { score += 15; reasons.push(`EMA9<EMA21`); }
    if (side === "BUY" && rsi14 && rsi14 >= 55 && rsi14 <= 72) { score += 10; reasons.push(`RSI ${rsi14}`); }
    if (side === "SHORT" && rsi14 && rsi14 <= 45 && rsi14 >= 28) { score += 10; reasons.push(`RSI ${rsi14}`); }
    if (side === "BUY" && asset.priceNum >= Number(sr.r1) * 0.998) { score += 10; reasons.push(`Near R1 (${sr.r1})`); }
    else if (side === "SHORT" && asset.priceNum <= Number(sr.s1) * 1.002) { score += 10; reasons.push(`Near S1 (${sr.s1})`); }

    const finalScore = Math.min(95, Math.max(25, score));
    const isConfirmed = finalScore >= 75;
    asset.probabilityScore = finalScore;

    if (badge && card) {
        card.classList.remove("radar-blink-buy", "radar-blink-sell");
        if (isConfirmed) {
            badge.textContent = `HEURISTIC ${finalScore}/100 • ${side}`;
            badge.className = side === "BUY"
                ? "text-[8.5px] font-mono font-black px-2 py-0.5 rounded-full bg-[#00d09c] text-white"
                : "text-[8.5px] font-mono font-black px-2 py-0.5 rounded-full bg-[#eb5b3c] text-white";
            card.classList.add(side === "BUY" ? "radar-blink-buy" : "radar-blink-sell");
        } else {
            badge.textContent = `${feedQual} • NO TRADE`;
            badge.className = "text-[8px] font-mono text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded border border-slate-200";
        }
    }
    return { score: finalScore, isConfirmed, side: isConfirmed ? side : "NO TRADE", reasons };
}

function playOpportunitySonarBeep(side = "BUY") {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = side === "BUY" ? "sine" : "triangle";
        osc.frequency.setValueAtTime(side === "BUY" ? 880 : 480, ctx.currentTime);
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
        osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.3);
    } catch (e) {}
}

let OPPORTUNITY_QUEUE = [];
let oppPopupCountdownSec = 15;
let oppPopupIntervalId = null;
let oppPopupPinnedAfterOrder = false;
let backendOpportunityPollTimer = null;
let backendAutomationScanTimer = null;
let backendAutomationScanBusy = false;

function enqueueOpportunities(list) {
    const existingKeys = new Set(OPPORTUNITY_QUEUE.map(x => x.orderKey));
    if (SESSION_VAULT.currentOppData?.orderKey) existingKeys.add(SESSION_VAULT.currentOppData.orderKey);
    const mapped = list
        .filter(x => x?.id && x?.expiresAt && x?.bengaliReason
            && x.executionEligible === true && x.isNetViable !== false && Number(x.fusionScore) >= 70
            && !existingKeys.has(x.orderKey))
        .map(x => ({
            ...x,
            isDemoTest: false,
            type: `${x.tierLabel || "Opportunity"} • ${x.side} • ${x.fusionScore}/100`,
            calibratedProbPct: x.fusionScore,
            reason: x.bengaliReason,
            freshness: "SERVER VERIFIED"
    }));

    OPPORTUNITY_QUEUE = [
        ...OPPORTUNITY_QUEUE.filter(q => !existingKeys.has(q.orderKey) && isOpportunityValid(q)),
        ...mapped
    ];
    const modal = document.getElementById("opportunityPopupModal");
    if (modal && modal.classList.contains("hidden") && OPPORTUNITY_QUEUE.length > 0) {
        showNextOpportunityFromQueue();
    }
}

function isOpportunityValid(opp) {
    if (!opp) return false;
    if (opp.isDemoTest) return true;
    return Boolean(opp.id && Date.parse(opp.expiresAt) > Date.now());
}

function showNextOpportunityFromQueue() {
    while (OPPORTUNITY_QUEUE.length > 0 && !isOpportunityValid(OPPORTUNITY_QUEUE[0])) {
        OPPORTUNITY_QUEUE.shift();
    }
    if (OPPORTUNITY_QUEUE.length === 0) {
        dismissOpportunityPopup();
        return;
    }
    const next = OPPORTUNITY_QUEUE.shift();
    triggerOpportunityAlert(next);
}

function advanceOpportunityQueue() {
    if (oppPopupIntervalId) clearInterval(oppPopupIntervalId);
    showNextOpportunityFromQueue();
}

function updateOppPopupRiskPreview() {
    const opp = SESSION_VAULT.currentOppData;
    const prevEl = document.getElementById("oppPopupRiskPreview");
    if (!opp || !prevEl) return;
    if (Number.isFinite(Number(opp.riskInr))) {
        prevEl.textContent = `R:R ${opp.rrRatio || "--"} • ₹${Math.round(Number(opp.riskInr)).toLocaleString("en-IN")}`;
        return;
    }
    const qty = Number(document.getElementById("oppPopupQtyInput")?.value || 0);
    const isCrypto = opp.orderKey.endsWith("USDT");
    const fx = isCrypto ? (FX_RATE_STATE.usdToInr || 86) : 1;
    const riskInr = Math.abs(Number(opp.entry) - Number(opp.sl)) * qty * fx;
    prevEl.textContent = `R:R ${opp.rrRatio || "--"} • ₹${Math.round(riskInr).toLocaleString("en-IN")}`;
}

function triggerOpportunityAlert(data) {
    SESSION_VAULT.currentOppData = data;
    const isBuy = data.side === "BUY";
    playOpportunitySonarBeep(isBuy ? "BUY" : "SELL");

    const modal = document.getElementById("opportunityPopupModal");
    const score = Number(data.fusionScore || data.calibratedProbPct || 0);
    document.getElementById("oppPopupScoreBadge").textContent = `Score: ${score}%`;
    document.getElementById("oppPopupTitle").textContent = data.symbol;
    document.getElementById("oppPopupDirBadge").textContent = data.side;
    document.getElementById("oppPopupDirBadge").className = `px-1.5 py-0.5 rounded font-black ${data.side === "BUY" ? "bg-[#00d09c]/20 text-[#059669]" : "bg-[#eb5b3c]/20 text-[#eb5b3c]"}`;
    document.getElementById("oppPopupEntry").textContent = data.entry;
    document.getElementById("oppPopupSL").textContent = data.sl;
    document.getElementById("oppPopupTarget").textContent = data.target;

    const rr = data.target && data.sl && data.entry ? ((data.target - data.entry) / (data.entry - data.sl)).toFixed(2) : "--";
    document.getElementById("oppPopupRrBadge").textContent = `R:R ${rr}`;
    document.getElementById("oppPopupRiskBadge").textContent = `₹${(data.quantity * (data.entry - data.sl)).toFixed(0)}`;

    const netProfit = Number.isFinite(data.netProfitAfterCosts) ? data.netProfitAfterCosts : null;
    const netBadge = document.getElementById("oppPopupNetProfitBadge");
    if (netBadge) {
        if (netProfit !== null) {
            netBadge.textContent = `Net: ₹${netProfit.toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            netBadge.className = netProfit > 0 ? "text-[#059669] font-black" : "text-[#eb5b3c] font-black";
        } else {
            netBadge.textContent = "Net: ₹--";
            netBadge.className = "text-[#059669] font-black";
        }
    }

    const riskVetoBadge = document.getElementById("oppPopupRiskVetoBadge");
    if (riskVetoBadge) {
        const isVetoed = data.isNetViable === false || data.costVetoReason || data.executionEligible === false;
        if (isVetoed) {
            riskVetoBadge.textContent = data.costVetoReason ? "AVOID RISK" : "RISK VETO";
            riskVetoBadge.classList.remove("hidden");
        } else {
            riskVetoBadge.classList.add("hidden");
        }
    }

    const execBtn = document.getElementById("btnExecuteOpp");
    if (execBtn) {
        if (data.isDemoTest) {
            execBtn.disabled = true;
            execBtn.className = "flex-1 bg-slate-200 text-slate-700 font-bold py-2 rounded-xl text-[9px] cursor-not-allowed";
            execBtn.innerHTML = `<i class="fa-solid fa-ban mr-1"></i> <span>DEMO</span>`;
        } else if (data.executionEligible !== true || score < 70) {
            execBtn.disabled = true;
            execBtn.className = "flex-1 bg-slate-200 text-slate-500 font-bold py-2 rounded-xl text-[9px] cursor-not-allowed";
            execBtn.innerHTML = `<i class="fa-solid fa-circle-info mr-1"></i> <span>Score < 70</span>`;
        } else {
            execBtn.disabled = false;
            execBtn.className = data.side === "BUY" ? "flex-1 groww-btn-green py-2 rounded-xl text-[9px] font-black" : "flex-1 groww-btn-red py-2 rounded-xl text-[9px] font-black";
            execBtn.innerHTML = `<i class="fa-solid fa-bolt mr-1"></i> <span>EXECUTE</span>`;
        }
    }

    oppPopupCountdownSec = 15;
    oppPopupPinnedAfterOrder = false;
    if (oppPopupIntervalId) clearInterval(oppPopupIntervalId);
    const timerBadge = document.getElementById("oppPopupTimerBadge");
    if (timerBadge) timerBadge.textContent = `⏱ ${oppPopupCountdownSec}s`;

    oppPopupIntervalId = setInterval(() => {
        if (oppPopupPinnedAfterOrder) return;
        const remainingMs = Date.parse(data.expiresAt) - Date.now();
        oppPopupCountdownSec = Math.max(0, Math.ceil(remainingMs / 1000));
        if (timerBadge) timerBadge.textContent = `⏱ ${oppPopupCountdownSec}s`;
        if (oppPopupCountdownSec <= 0) {
            advanceOpportunityQueue();
        }
    }, 1000);

    modal?.classList.remove("hidden");
}

async function testOpportunityAlert() {
    if (!checkProAccess()) return;
    const asset = currentSelectedAsset || MASTER_ASSETS[0];
    if (!asset || asset.priceNum === 0) {
        alert("Market feed offline/connecting. Wait for valid price tick.");
        return;
    }
    const sr = calculateDynamicSupportResistance(asset.high, asset.low, asset.priceNum);
    triggerOpportunityAlert({
        isDemoTest: true,
        expiresAt: new Date(Date.now() + 15000).toISOString(),
        assetId: asset.id,
        symbol: `${asset.name} [TEST]`,
        orderKey: asset.orderKey,
        exchange: "SIM",
        type: "DEMO SIMULATION",
        side: "BUY",
        reason: "Explicit Demo Simulation Alert — Cannot route live broker orders.",
        entry: asset.priceNum.toFixed(2),
        sl: sr.s1,
        target: sr.r1,
        rrRatio: "1:1.5"
    });
}

function repeatOpportunityVoice() {
    if (SESSION_VAULT.currentOppData) {
        const d = SESSION_VAULT.currentOppData;
        explainTextFriendly(`${d.symbol} ${d.side} entry ${d.entry}, stop loss ${d.sl}, target ${d.target}`, d.symbol);
    }
}
function dismissOpportunityPopup() {
    if (oppPopupIntervalId) clearInterval(oppPopupIntervalId);
    oppPopupPinnedAfterOrder = false;
    document.getElementById("opportunityPopupModal")?.classList.add("hidden");
    SESSION_VAULT.currentOppData = null;
    stopAiVoice();
}
function dismissOpportunityToast() { document.getElementById("liveOpportunityToast")?.classList.add("hidden"); }
function scrollToCurrentOpportunityCard() { dismissOpportunityToast(); document.getElementById("opportunityPopupModal")?.classList.remove("hidden"); }

async function executeOpportunityTradeNow() {
    const opp = SESSION_VAULT.currentOppData;
    if (!opp) return;
    if (opp.isDemoTest) {
        dismissOpportunityPopup();
        return;
    }
    if (opp.executionEligible !== true || Number(opp.fusionScore || 0) < 70) {
        return;
    }
    if (!isOpportunityValid(opp)) {
        alert("Signal expired or price drifted! Advancing to next valid opportunity.");
        advanceOpportunityQueue();
        return;
    }

    oppPopupPinnedAfterOrder = true;
    if (oppPopupIntervalId) clearInterval(oppPopupIntervalId);
    const response = await fastApiRequest(`/automation/opportunities/${encodeURIComponent(opp.id)}/claim`, {
        method: "POST",
        body: {}
    });
    if (!response.ok || !response.data?.success) {
        oppPopupPinnedAfterOrder = false;
        alert(response.data?.detail?.code === "OPPORTUNITY_EXPIRED_OR_CLAIMED"
            ? "This opportunity's 15-second window has expired."
            : response.data?.detail || "Paper trade could not be submitted.");
        advanceOpportunityQueue();
        return;
    }
    // Save to execution log
    await saveExecutionLogToBackend({
        symbol: opp.symbol,
        side: opp.side,
        quantity: opp.quantity,
        entry_price: opp.entry,
        stop_loss: opp.sl,
        take_profit: opp.target,
        score: opp.fusionScore || opp.calibratedProbPct || 0,
        status: "CLAIMED",
        is_auto_trade: false
    });
    alert(`Paper order accepted: ${opp.symbol} • Qty ${opp.quantity}`);
    dismissOpportunityPopup();
}

let DYNAMIC_SCAN_CACHE = [];
let SCANNER_UNIVERSE_COUNT = 0;
let lastScannerRunTs = 0;
let isDeepScanRunning = false;
let DEEP_SCAN_ROTATION_CURSOR = { indian: 0, crypto: 0 };
const DEEP_ANALYZED_SYMBOLS_SET = new Set();
let SCANNER_COVERAGE = {
    expectedUniverse: 0, discovered: 0, scanAttempted: 0, baselineScanned: 0,
    eligibleCount: 0, deepAnalyzedCount: 0, skippedCount: 0, failedCount: 0,
    coverageGap: 0, nseCount: 0, bseCount: 0, cryptoCount: 0, asOf: 0, source: "NONE"
};
let GLOBAL_SEARCH_STATE = { query: "", exchange: "ALL", page: 1, pageSize: 20, totalMatches: 0, totalPages: 1, items: [] };

function analyzeCandlestickAndStrategyConfluence(bars, meta = {}) {
    const closedBars = Array.isArray(bars) ? bars.filter(b => b && b.isClosed !== false) : [];
    if (closedBars.length < 15) {
        return { score: 0, direction: "NO TRADE", optionHint: "WAIT", patterns: [], strategies: [], reasons: ["Insufficient closed OHLCV bars"], entry: 0, sl: 0, target: 0, rrRatio: "--", trapType: null };
    }
    bars = closedBars;
    const n = bars.length;
    const c = bars[n - 1];
    const p1 = bars[n - 2];
    const p2 = bars[n - 3];

    const body = (b) => Math.abs(b.close - b.open);
    const range = (b) => Math.max(0.00001, b.high - b.low);
    const upperWick = (b) => b.high - Math.max(b.open, b.close);
    const lowerWick = (b) => Math.min(b.open, b.close) - b.low;
    const isBull = (b) => b.close > b.open;
    const isBear = (b) => b.close < b.open;

    const patterns = [];
    let bullPts = 0;
    let bearPts = 0;

    // 1. Single-Bar Patterns
    const rC = range(c), bC = body(c), uW = upperWick(c), lW = lowerWick(c);
    if (bC <= rC * 0.12) patterns.push("Doji");
    if (bC <= rC * 0.12 && lW >= rC * 0.60 && uW <= rC * 0.12) { patterns.push("Dragonfly Doji"); bullPts += 14; }
    if (bC <= rC * 0.12 && uW >= rC * 0.60 && lW <= rC * 0.12) { patterns.push("Gravestone Doji"); bearPts += 14; }
    if (lW >= bC * 2.1 && uW <= rC * 0.15 && c.close < p1.close) { patterns.push("Hammer"); bullPts += 18; }
    if (uW >= bC * 2.1 && lW <= rC * 0.15 && c.close < p1.close) { patterns.push("Inverted Hammer"); bullPts += 12; }
    if (uW >= bC * 2.1 && lW <= rC * 0.15 && c.close > p1.close) { patterns.push("Shooting Star"); bearPts += 18; }
    if (lW >= bC * 2.1 && uW <= rC * 0.15 && c.close > p1.close) { patterns.push("Hanging Man"); bearPts += 12; }
    if (isBull(c) && bC >= rC * 0.82) { patterns.push("Bullish Marubozu"); bullPts += 18; }
    if (isBear(c) && bC >= rC * 0.82) { patterns.push("Bearish Marubozu"); bearPts += 18; }

    // 2. Two-Bar Patterns
    if (isBear(p1) && isBull(c) && c.close >= p1.open && c.open <= p1.close && bC > body(p1)) { patterns.push("Bullish Engulfing"); bullPts += 24; }
    if (isBull(p1) && isBear(c) && c.open >= p1.close && c.close <= p1.open && bC > body(p1)) { patterns.push("Bearish Engulfing"); bearPts += 24; }
    if (isBear(p1) && isBull(c) && c.open < p1.low && c.close > (p1.open + p1.close) / 2 && c.close < p1.open) { patterns.push("Piercing Line"); bullPts += 18; }
    if (isBull(p1) && isBear(c) && c.open > p1.high && c.close < (p1.open + p1.close) / 2 && c.close > p1.open) { patterns.push("Dark Cloud Cover"); bearPts += 18; }
    if (isBear(p1) && isBull(c) && c.open > p1.close && c.close < p1.open && bC < body(p1) * 0.6) { patterns.push("Bullish Harami"); bullPts += 14; }
    if (isBull(p1) && isBear(c) && c.open < p1.close && c.close > p1.open && bC < body(p1) * 0.6) { patterns.push("Bearish Harami"); bearPts += 14; }
    if (Math.abs(c.low - p1.low) <= rC * 0.08 && isBear(p1) && isBull(c)) { patterns.push("Tweezer Bottom"); bullPts += 16; }
    if (Math.abs(c.high - p1.high) <= rC * 0.08 && isBull(p1) && isBear(c)) { patterns.push("Tweezer Top"); bearPts += 16; }
    if (c.high < p1.high && c.low > p1.low) patterns.push("Inside Bar");
    if (isBear(p1) && isBull(c) && c.open > p1.open) { patterns.push("Bullish Kicker"); bullPts += 22; }
    if (isBull(p1) && isBear(c) && c.open < p1.open) { patterns.push("Bearish Kicker"); bearPts += 22; }

    // 3. Multi-Bar & Squeeze Patterns
    if (isBear(p2) && body(p1) <= body(p2) * 0.45 && isBull(c) && c.close > (p2.open + p2.close) / 2) { patterns.push("Morning Star"); bullPts += 25; }
    if (isBull(p2) && body(p1) <= body(p2) * 0.45 && isBear(c) && c.close < (p2.open + p2.close) / 2) { patterns.push("Evening Star"); bearPts += 25; }
    if (isBull(p2) && isBull(p1) && isBull(c) && c.close > p1.close && p1.close > p2.close) { patterns.push("Three White Soldiers"); bullPts += 22; }
    if (isBear(p2) && isBear(p1) && isBear(c) && c.close < p1.close && p1.close < p2.close) { patterns.push("Three Black Crows"); bearPts += 22; }

    const last7Ranges = bars.slice(-8, -1).map(b => b.high - b.low);
    if (last7Ranges.length === 7 && range(p1) <= Math.min(...last7Ranges)) {
        if (c.close > p1.high) { patterns.push("NR7 Breakout Up"); bullPts += 20; }
        else if (c.close < p1.low) { patterns.push("NR7 Breakdown"); bearPts += 20; }
    }

    // 4. Quantitative Strategy Confluence (EMA, RSI, RVOL, ATR, Trap/Sweep)
    const closes = bars.map(b => b.close);
    const ema9 = computeEMA(closes, 9);
    const ema21 = computeEMA(closes, 21);
    const rsi14 = computeRSI(closes, 14);
    const strategies = [];

    if (ema9 && ema21) {
        if (ema9 > ema21 && c.close > ema9) { bullPts += 22; strategies.push(`EMA9>EMA21`); }
        else if (ema9 < ema21 && c.close < ema9) { bearPts += 22; strategies.push(`EMA9<EMA21`); }
    }
    if (rsi14 !== null) {
        if (rsi14 >= 54 && rsi14 <= 74) { bullPts += 16; strategies.push(`RSI ${rsi14} Bull`); }
        else if (rsi14 <= 46 && rsi14 >= 26) { bearPts += 16; strategies.push(`RSI ${rsi14} Bear`); }
        else if (rsi14 < 28 && bullPts > 0) { bullPts += 14; strategies.push(`RSI Oversold Bounce`); }
        else if (rsi14 > 74 && bearPts > 0) { bearPts += 14; strategies.push(`RSI Overbought Reversal`); }
    }

    const rvol = (meta.rvol !== null && meta.rvol !== undefined && Number.isFinite(Number(meta.rvol))) ? Number(meta.rvol) : null;
    if (rvol !== null && rvol >= 1.5) {
        if (bullPts > bearPts) bullPts += 16;
        else if (bearPts > bullPts) bearPts += 16;
        strategies.push(`RVOL ${rvol.toFixed(1)}x`);
    }

    // Liquidity Sweep & Trap Detection
    const prior10 = bars.slice(-11, -1);
    let trapType = null;
    if (prior10.length >= 6) {
        const sH = Math.max(...prior10.map(b => b.high));
        const sL = Math.min(...prior10.map(b => b.low));
        if (c.high > sH && c.close < sH) { trapType = "Bull Trap / Buy-Side Sweep"; bearPts += 20; strategies.push(trapType); }
        if (c.low < sL && c.close > sL) { trapType = "Bear Trap / Sell-Side Sweep"; bullPts += 20; strategies.push(trapType); }
    }

    // Orderbook depth boost if available
    const l2 = LIVE_L2_DEPTH_MAP[meta.orderKey];
    if (l2 && (Date.now() - l2.updatedAt < 15000)) {
        if (l2.buyers >= 62) { bullPts += 15; strategies.push(`Bid Depth ${l2.buyers}%`); }
        if (l2.sellers >= 62) { bearPts += 15; strategies.push(`Ask Depth ${l2.sellers}%`); }
    }

    // Compute ATR for Auto Protective SL & Target
    let trSum = 0;
    for (let i = Math.max(1, n - 14); i < n; i++) {
        trSum += Math.max(bars[i].high - bars[i].low, Math.abs(bars[i].high - bars[i - 1].close), Math.abs(bars[i].low - bars[i - 1].close));
    }
    const atr = Math.max(c.close * 0.003, trSum / Math.min(14, n - 1));
    const ltp = c.close;

    let direction = "NO TRADE";
    let optionHint = "WAIT";
    let score = Math.min(96, Math.max(bullPts, bearPts));
    let sl = ltp;
    let target = ltp;

    const isIndian = meta.category === "indian";
    if (bullPts >= 70 && bullPts >= bearPts + 15) {
        direction = "BUY";
        optionHint = isIndian ? "BUY / CALL / LONG" : "BUY / LONG";
        sl = ltp - atr * 1.5;
        target = ltp + atr * 2.8;
    } else if (bearPts >= 70 && bearPts >= bullPts + 15) {
        direction = "SELL";
        optionHint = isIndian ? "SELL / PUT / SHORT" : "SELL / SHORT";
        sl = ltp + atr * 1.5;
        target = ltp - atr * 2.8;
    }

    const dec = ltp < 10 ? 4 : 2;
    const riskDist = Math.abs(ltp - sl);
    const rewDist = Math.abs(target - ltp);
    const rrRatio = riskDist > 0 ? `1:${(rewDist / riskDist).toFixed(1)}` : "--";

    return {
        score,
        direction,
        optionHint,
        patterns,
        strategies,
        reasons: [...patterns, ...strategies].slice(0, 4),
        entry: Number(ltp.toFixed(dec)),
        sl: Number(sl.toFixed(dec)),
        target: Number(target.toFixed(dec)),
        atr: Number(atr.toFixed(dec)),
        rrRatio,
        trapType
    };
}

async function fetchQuickCandlesForScanner(item) {
    try {
        const nowMs = Date.now();
        if (item.category === "crypto" || item.category === "commodity") {
            const sym = item.orderKey === "PAXG" ? "PAXGUSDT" : item.orderKey;
            if (SESSION_VAULT.coindcxLinked && auth.currentUser) {
                const cRes = await secureApiCall("historical_candles", {
                    method: "GET",
                    query: `symbol=${encodeURIComponent(sym)}&timeframe=15m&range=5d`
                });
                const currentBucketStart = Math.floor(nowMs / 900000) * 900000;
                if (cRes.ok && cRes.data?.success && Array.isArray(cRes.data.candles)) {
                    return cRes.data.candles.filter(c => Number(c.time) < currentBucketStart).map(c => ({ ...c, isClosed: true })).slice(-60);
                }
            }
            const r = await fetch(`https://api.binance.com/api/v3/klines?symbol=${encodeURIComponent(sym)}&interval=15m&limit=200`);
            if (!r.ok) return [];
            const raw = await r.json();
            if (!Array.isArray(raw)) return [];
            return raw
                .filter(k => Number(k[6]) < nowMs)
                .map(k => ({ time: +k[0], open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5], isClosed: true }))
                .slice(-60);
        }
        const ySym = item.yahooSymbol || (item.orderKey === "NIFTY" ? "^NSEI" : item.orderKey === "BANKNIFTY" ? "^NSEBANK" : item.orderKey === "SENSEX" ? "^BSESN" : `${item.orderKey}.NS`);
        let j = null;
        if (SESSION_VAULT.shoonyaLinked && auth.currentUser) {
            const sRes = await secureApiCall("historical_candles", {
                method: "GET",
                query: `symbol=${encodeURIComponent(item.orderKey)}&yahooSymbol=${encodeURIComponent(ySym)}&timeframe=15m&range=5d`
            });
            j = sRes.data;
        } else {
            const r = await secureApiCall("historical_candles", {
                method: "GET",
                query: `symbol=${encodeURIComponent(item.orderKey)}&yahooSymbol=${encodeURIComponent(ySym)}&timeframe=15m&range=5d`
            });
            j = r.ok ? r.data : null;
        }
        if (!j?.success || !Array.isArray(j.candles)) return [];
        const currentBucketStart = Math.floor(nowMs / 900000) * 900000;
        return j.candles
            .filter(c => Number(c.time) < currentBucketStart)
            .map(c => ({ ...c, isClosed: true }))
            .slice(-60);
    } catch (e) {
        return [];
    }
}
function updateUniverseCoverageDashboard() {
    const c = SCANNER_COVERAGE;
    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const indSess = getExchangeSessionStatus("indian");
    const sessBadge = document.getElementById("covSessionBadge");
    if (sessBadge) {
        sessBadge.textContent = `NSE/BSE: ${indSess.state} • CRYPTO: 24/7`;
        sessBadge.className = indSess.isOpen
            ? "px-1.5 py-0.2 rounded bg-[#00d09c]/15 text-[#059669] font-bold"
            : "px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-700 font-bold";
    }
    setTxt("covExpected", String(c.expectedUniverse || 0));
    setTxt("covScanned", `${c.baselineScanned || 0} (NSE:${c.nseCount || 0} BSE:${c.bseCount || 0} CRY:${c.cryptoCount || 0})`);
    setTxt("covDeep", `${c.deepAnalyzedCount || 0} / ${c.eligibleCount || 0}`);
    setTxt("covGapSkip", `${c.coverageGap || 0} / ${c.skippedCount || 0}`);

    const warnEl = document.getElementById("covWarningBanner");
    if (warnEl) {
        if ((c.coverageGap || 0) > 0 || (c.failedCount || 0) > 0) {
            warnEl.classList.remove("hidden");
            warnEl.textContent = `⚠️ Coverage Gap: ${c.coverageGap} • Failed: ${c.failedCount} • As-of: ${c.asOf ? new Date(c.asOf).toLocaleTimeString("en-IN") : "--"}`;
        } else {
            warnEl.classList.add("hidden");
        }
    }
}

async function runFullMarketDiamondScan(isManual = false) {
    if (isDeepScanRunning) return;
    isDeepScanRunning = true;
    const scanBtn = document.querySelector('button[onclick*="runFullMarketDiamondScan"]');
    if (scanBtn) { scanBtn.disabled = true; scanBtn.innerHTML = `<i class="fa-solid fa-spinner animate-spin text-[10px]"></i> <span>Scanning...</span>`; }

    try {
        const indSession = getExchangeSessionStatus("indian");
        const prevOppMap = new Map(DYNAMIC_SCAN_CACHE.filter(x => x.opp).map(x => [x.symbol, x.opp]));

        const [indP1Res, binRes] = await Promise.allSettled([
            secureApiCall("deep_scanner", {
                method: "GET",
                query: "eligibleOnly=1&page=1&pageSize=500"
            }).then(r => r.ok ? r.data : null).catch(() => null),
            fetch("https://api.binance.com/api/v3/ticker/24hr").then(r => r.ok ? r.json() : null).catch(() => null)
        ]);

        const universeItems = [];
        const seenSymbols = new Set();

        MASTER_ASSETS.forEach(a => {
            if (a.priceNum > 0 && !seenSymbols.has(a.symbol)) {
                seenSymbols.add(a.symbol);
                universeItems.push({
                    id: a.id, orderKey: a.orderKey, symbol: a.symbol, yahooSymbol: a.yahooSymbol || "",
                    name: a.name, exchange: a.symbol.split(":")[0], category: a.category,
                    price: a.priceNum, priceFormatted: a.price,
                    high: a.high || a.priceNum, low: a.low || a.priceNum,
                    changePct: parseFloat(a.change) || 0, change: a.change || "0.00%",
                    volume: null, turnover: 0, rvol: null, atr: null,
                    sector: a.category === "indian" ? "Index / Core" : "Crypto Core",
                    eligible: true, dataQuality: a.dataEnvelope?.quality || "LIVE",
                    opp: prevOppMap.get(a.symbol) || null
                });
            }
        });

        let expectedInd = 0, discoveredInd = 0, baselineInd = 0, eligibleInd = 0, skippedInd = 0, failedInd = 0, gapInd = 0, nseCnt = 0, bseCnt = 0, indAsOf = 0, indSource = "NONE";
        const indData = indP1Res.status === "fulfilled" ? indP1Res.value : null;

        if (indData?.success && Array.isArray(indData.items)) {
            expectedInd = Number(indData.expectedUniverse || 0);
            discoveredInd = Number(indData.discovered || 0);
            baselineInd = Number(indData.baselineScanned || 0);
            eligibleInd = Number(indData.eligibleCount || 0);
            skippedInd = Number(indData.skippedCount || 0);
            failedInd = Number(indData.failedCount || 0);
            gapInd = Number(indData.coverageGap || 0);
            nseCnt = Number(indData.nseCount || 0);
            bseCnt = Number(indData.bseCount || 0);
            indAsOf = Number(indData.asOf || Date.now());
            indSource = indData.source || "TRADINGVIEW_INDIA";

            let allIndItems = [...indData.items];
            const totalPages = Math.min(8, Number(indData.totalPages || 1));
            if (totalPages > 1) {
                const extraCalls = [];
                for (let p = 2; p <= totalPages; p++) {
                    extraCalls.push(secureApiCall("deep_scanner", {
                        method: "GET",
                        query: `eligibleOnly=1&page=${p}&pageSize=500`
                    }).then(r => r.ok ? r.data : null).catch(() => null));
                }
                const extraRes = await Promise.allSettled(extraCalls);
                extraRes.forEach(r => {
                    if (r.status === "fulfilled" && Array.isArray(r.value?.items)) {
                        allIndItems = allIndItems.concat(r.value.items);
                    }
                });
            }

            allIndItems.forEach(it => {
                if (seenSymbols.has(it.symbol)) {
                    const ex = universeItems.find(x => x.symbol === it.symbol);
                    if (ex) {
                        ex.rvol = it.rvol !== undefined ? it.rvol : ex.rvol;
                        ex.atr = it.atr !== undefined ? it.atr : ex.atr;
                        ex.volume = it.volume !== undefined ? it.volume : ex.volume;
                        ex.turnover = it.turnoverInr || ex.turnover;
                        ex.sector = it.sector || ex.sector;
                    }
                    return;
                }
                seenSymbols.add(it.symbol);
                universeItems.push({
                    id: it.id, orderKey: it.orderKey, symbol: it.symbol, yahooSymbol: it.yahooSymbol,
                    name: it.name, exchange: it.exchange, category: "indian",
                    price: it.price, priceFormatted: `₹${it.price.toFixed(2)}`,
                    high: it.high, low: it.low, changePct: it.changePct, change: it.change,
                    volume: it.volume, turnover: it.turnoverInr || 0,
                    rvol: (it.rvol !== undefined ? it.rvol : null),
                    atr: (it.atr !== undefined ? it.atr : null),
                    sector: it.sector || "NSE/BSE Equity",
                    eligible: it.eligible !== false,
                    dataQuality: indSession.isOpen ? "DELAYED" : "CLOSED_SESSION",
                    opp: prevOppMap.get(it.symbol) || null
                });
            });
        }

        let cryptoEligibleCount = 0;
        const useCoinDcxCrypto = SESSION_VAULT.coindcxLinked && Array.isArray(window.LAST_COINDCX_ALL_USDT) && window.LAST_COINDCX_ALL_USDT.length > 0;
        if (useCoinDcxCrypto) {
            cryptoEligibleCount = window.LAST_COINDCX_ALL_USDT.length;
            window.LAST_COINDCX_ALL_USDT.forEach(t => {
                const sym = t.orderKey;
                const fullSym = `COINDCX:${sym}`;
                const lp = Number(t.price);
                if (seenSymbols.has(fullSym)) return;
                seenSymbols.add(fullSym);
                universeItems.push({
                    id: `cry_${sym.toLowerCase()}`, orderKey: sym, symbol: fullSym, yahooSymbol: `${sym.replace(/USDT$/, "")}-USD`,
                    name: `${sym.replace(/USDT$/, "")} / USDT`, exchange: "COINDCX", category: "crypto",
                    price: lp, priceFormatted: lp < 1 ? `$${lp.toFixed(4)}` : `$${lp.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                    high: Number(t.high || lp), low: Number(t.low || lp),
                    changePct: Number(t.changePct || 0), change: `${t.changePct >= 0 ? "+" : ""}${Number(t.changePct || 0).toFixed(2)}%`,
                    volume: Number(t.volume || 0), turnover: Number(t.volume || 0) * lp,
                    rvol: null, atr: null,
                    sector: "CoinDCX Crypto",
                    eligible: true,
                    dataQuality: "LIVE",
                    opp: prevOppMap.get(fullSym) || null
                });
            });
        } else {
            const binData = binRes.status === "fulfilled" ? binRes.value : null;
            if (Array.isArray(binData)) {
                const usdtPairs = binData.filter(t =>
                    t.symbol && t.symbol.endsWith("USDT") &&
                    !/UPUSDT$|DOWNUSDT$|BEARUSDT$|BULLUSDT$|USDCUSDT$|FDUSDUSDT$|TUSDUSDT$/.test(t.symbol) &&
                    Number(t.quoteVolume || 0) >= 5000000 && Number(t.lastPrice || 0) > 0
                );
                cryptoEligibleCount = usdtPairs.length;
                usdtPairs.sort((a, b) => Number(b.quoteVolume) - Number(a.quoteVolume)).forEach(t => {
                    const sym = t.symbol.toUpperCase();
                    const fullSym = `BINANCE:${sym}`;
                    const lp = Number(t.lastPrice);
                    const chg = Number(t.priceChangePercent || 0);
                    if (seenSymbols.has(fullSym)) {
                        const ex = universeItems.find(x => x.symbol === fullSym);
                        if (ex) { ex.turnover = Number(t.quoteVolume); ex.volume = Number(t.volume || 0); }
                        return;
                    }
                    seenSymbols.add(fullSym);
                    universeItems.push({
                        id: `cry_${sym.toLowerCase()}`, orderKey: sym, symbol: fullSym, yahooSymbol: `${sym.replace(/USDT$/, "")}-USD`,
                        name: `${sym.replace(/USDT$/, "")} / USDT`, exchange: "BINANCE", category: "crypto",
                        price: lp, priceFormatted: lp < 1 ? `$${lp.toFixed(4)}` : `$${lp.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                        high: Number(t.highPrice || lp), low: Number(t.lowPrice || lp),
                        changePct: Number(chg.toFixed(2)), change: `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`,
                        volume: Number(t.volume || 0), turnover: Number(t.quoteVolume || 0),
                        rvol: null, atr: null,
                        sector: "Crypto Spot/Perp",
                        eligible: true,
                        dataQuality: "LIVE",
                        opp: prevOppMap.get(fullSym) || null
                    });
                });
            }
        }

        DYNAMIC_SCAN_CACHE = universeItems;

        // Fair Rotating Deep-Analysis Queue (No starvation!)
        const indPool = universeItems.filter(x => x.category === "indian" && x.eligible !== false);
        const cryPool = universeItems.filter(x => (x.category === "crypto" || x.category === "commodity") && x.eligible !== false);

        const pickBatch = (pool, cursorKey, hotCount, rotCount) => {
            if (!pool.length) return [];
            const sortedHot = [...pool].sort((a, b) => ((b.rvol || 0) + Math.abs(b.changePct) * 0.4) - ((a.rvol || 0) + Math.abs(a.changePct) * 0.4)).slice(0, hotCount);
            const rot = [];
            const start = DEEP_SCAN_ROTATION_CURSOR[cursorKey] % pool.length;
            for (let i = 0; i < Math.min(rotCount, pool.length); i++) {
                rot.push(pool[(start + i) % pool.length]);
            }
            DEEP_SCAN_ROTATION_CURSOR[cursorKey] = (start + rot.length) % pool.length;
            const merged = new Map();
            [...sortedHot, ...rot].forEach(x => merged.set(x.symbol, x));
            return Array.from(merged.values());
        };

        const indBatch = (indSession.isOpen || isManual || DEEP_ANALYZED_SYMBOLS_SET.size < 20) ? pickBatch(indPool, "indian", 6, 10) : [];
        const cryBatch = pickBatch(cryPool, "crypto", 6, 8);
        const candidates = [...indBatch, ...cryBatch];

        for (let i = 0; i < candidates.length; i += 5) {
            const batch = candidates.slice(i, i + 5);
            await Promise.allSettled(batch.map(async (item) => {
                let bars = getClosedBars(item.id, "15m");
                if (!bars || bars.length < 40) {
                    bars = await fetchQuickCandlesForScanner(item);
                    if (bars.length > 0) {
                        if (!QUANT_FEATURE_STORE.candlesByTf[item.id]) {
                            QUANT_FEATURE_STORE.candlesByTf[item.id] = { "1m": [], "3m": [], "5m": [], "15m": [], "30m": [], "1h": [], "4h": [], "1d": [] };
                        }
                        QUANT_FEATURE_STORE.candlesByTf[item.id]["15m"] = bars;
                    }
                }
                item.opp = analyzeCandlestickAndStrategyConfluence(bars, item);
                item.wf = runWalkForwardValidation(item.id);
                if (item.opp) {
                    item.opp.calibratedProbPct = item.wf?.calibratedProbPct ?? null;
                    item.opp.wfStatus = item.wf?.status || "INSUFFICIENT_DATA";
                    item.opp.sampleCount = item.wf?.trades || 0;
                    item.opp.modelVersion = item.wf?.modelVersion || "E55_WF_BUYSELL_v2.0";
                    item.opp.horizonBars = item.wf?.horizonBars || 8;
                    item.opp.ciText = item.wf?.ciLowerPct !== null ? `${item.wf.ciLowerPct}-${item.wf.ciUpperPct}%` : "--";
                }
                if (typeof runAdditiveEnginesPipeline === "function" && item.opp) {
                    const pseudoAsset = {
                        id: item.id, symbol: item.symbol, orderKey: item.orderKey, name: item.name,
                        category: item.category, priceNum: item.price,
                        dataEnvelope: { source: item.exchange, quality: item.dataQuality, eventTime: Date.now() }
                    };
                    const scanVol = computeVolatilityEngine(bars, item.category, 14);
                    const scanVolIntel = computeVolumeIntelligence(bars, pseudoAsset);
                    const scanRegime = detectMarketRegime(bars, scanVol, scanVolIntel);
                    const scanCorr = computeCorrelationEngine(item.id, item.category === "indian" ? "nifty50" : "btcusdt", 20);
                    item.fusionReport = runAdditiveEnginesPipeline(pseudoAsset, item.opp, {
                        volatility: scanVol,
                        volIntel: scanVolIntel,
                        regime: scanRegime,
                        corr: scanCorr,
                        walkFwd: item.wf
                    });
                }
                DEEP_ANALYZED_SYMBOLS_SET.add(item.symbol);
            }));
        }

        DYNAMIC_SCAN_CACHE = universeItems;
        SCANNER_COVERAGE = {
            expectedUniverse: expectedInd + cryptoEligibleCount,
            discovered: discoveredInd + cryptoEligibleCount,
            scanAttempted: baselineInd + cryptoEligibleCount,
            baselineScanned: baselineInd + cryptoEligibleCount,
            eligibleCount: eligibleInd + cryptoEligibleCount,
            deepAnalyzedCount: DEEP_ANALYZED_SYMBOLS_SET.size,
            skippedCount: skippedInd,
            failedCount: failedInd,
            coverageGap: gapInd,
            nseCount: nseCnt,
            bseCount: bseCnt,
            cryptoCount: cryptoEligibleCount,
            asOf: indAsOf || Date.now(),
            source: indSource
        };
        SCANNER_UNIVERSE_COUNT = SCANNER_COVERAGE.baselineScanned;
        lastScannerRunTs = Date.now();
        updateUniverseCoverageDashboard();

        if (!GLOBAL_SEARCH_STATE.query) renderDynamicScanner();
    } finally {
        isDeepScanRunning = false;
        if (scanBtn) { scanBtn.disabled = false; scanBtn.innerHTML = `<i class="fa-solid fa-crosshairs text-[10px]"></i> <span>Scan Now</span>`; }
    }
}

function autoFillOpportunityToDesk(orderKey, side, slPrice, targetPrice, symbolFull) {
    if (!checkProAccess()) return;
    const canonical = resolveCanonicalInstrument(orderKey);
    const selectEl = document.getElementById("tradeOrderSymbol");
    if (selectEl) {
        if (!Array.from(selectEl.options).some(o => o.value === orderKey)) {
            const opt = document.createElement("option");
            opt.value = orderKey;
            opt.textContent = `${orderKey} (${canonical?.exchange || "DYNAMIC"})`;
            selectEl.appendChild(opt);
        }
        selectEl.value = orderKey;
        handleOrderSymbolChange();
    }
    const slInp = document.getElementById("tradeSlPriceInput");
    if (slInp && slPrice > 0) slInp.value = String(slPrice);
    calculateSafeQtySuggestion();

    const item = DYNAMIC_SCAN_CACHE.find(x => x.orderKey === orderKey);
    if (item && item.opp) {
        SESSION_VAULT.activeBracketOrders[orderKey] = {
            bracketId: `BRK_${Date.now()}`, symbol: orderKey, side: side === "SELL" ? "SELL" : "BUY",
            entry: item.opp.entry, sl: Number(slPrice), target: Number(targetPrice),
            qty: canonical?.lotSize || 1, isActive: false, state: "PREFILLED"
        };
    }

    switchTab("ShoonyaPos");
    const statusBox = document.getElementById("orderStatusMsg");
    if (statusBox) {
        statusBox.className = "p-2 rounded-xl text-[10px] mono-font font-bold bg-[#00d09c]/15 border border-[#00d09c]/40 text-[#059669] block";
        statusBox.textContent = `Auto-Filled ${orderKey} (${side}) • Auto SL: ${slPrice} • Auto TG: ${targetPrice}. Tap BUY or SELL when ready.`;
    }
}

async function selectAssetForAnalysis(symbol, orderKey) {
    let found = MASTER_ASSETS.find(a => a.symbol === symbol) || MASTER_ASSETS.find(a => a.orderKey === orderKey);
    if (!found) {
        const scanned = DYNAMIC_SCAN_CACHE.find(x => x.symbol === symbol) || GLOBAL_SEARCH_STATE.items.find(x => x.symbol === symbol) || DYNAMIC_SCAN_CACHE.find(x => x.orderKey === orderKey);
        if (scanned) {
            found = {
                id: scanned.id, name: scanned.name, category: scanned.category, symbol: scanned.symbol,
                yahooSymbol: scanned.yahooSymbol, orderKey: scanned.orderKey, price: scanned.priceFormatted,
                priceNum: scanned.price, high: scanned.high, low: scanned.low,
                highFormatted: String(scanned.high), lowFormatted: String(scanned.low),
                change: scanned.change, dataEnvelope: { quality: scanned.dataQuality || "DELAYED", source: scanned.exchange }
            };
        } else {
            found = MASTER_ASSETS[0];
        }
    }
    currentSelectedAsset = found;
    initTradingViewChart(symbol, found.yahooSymbol || "");

    const orderSym = document.getElementById("tradeOrderSymbol");
    if (orderSym) {
        if (!Array.from(orderSym.options).some(o => o.value === orderKey)) {
            const opt = document.createElement("option");
            opt.value = orderKey;
            opt.textContent = `${orderKey} (${found.symbol.split(":")[0]})`;
            orderSym.appendChild(opt);
        }
        orderSym.value = orderKey;
        handleOrderSymbolChange();
    }

    const sr = calculateDynamicSupportResistance(found.high, found.low, found.priceNum);
    const flow = await fetchRealOrderflowDelta(found);
    const qSnap = await runQuantOrchestrator(found.id);
    const evalData = evaluateUltraOpportunity(found, flow, sr, qSnap);

    const alertBadge = document.getElementById("alertAssetBadge");
    const alertText = document.getElementById("alertAssetText");
    if (alertBadge && alertText) {
        const dirLabel = qSnap?.twoWay?.direction || evalData.side;
        alertBadge.textContent = `${found.name}: ${dirLabel} (${evalData.score}%)`;
        alertText.textContent = `${qSnap?.twoWay?.reasons?.join(" • ") || evalData.reasons?.join(" • ") || `LTP: ${found.price}`} [${found.dataEnvelope?.quality || "OFFLINE"}]`;
    }
    const targetLabel = document.getElementById("newsActiveTargetLabel");
    if (targetLabel) targetLabel.textContent = `${found.name} WIRE`;
}

let searchDebounceTimer = null;

function setSearchExchangeFilter(exch) {
    GLOBAL_SEARCH_STATE.exchange = exch;
    GLOBAL_SEARCH_STATE.page = 1;
    ["ALL", "NSE", "BSE", "BINANCE", "NFO"].forEach(e => {
        const b = document.getElementById(`srchExchBtn_${e}`);
        if (b) b.className = e === exch
            ? "px-2 py-0.5 rounded bg-[#00d09c] text-white font-black"
            : "px-2 py-0.5 rounded bg-white text-slate-600 border border-slate-200";
    });
    executeGlobalCatalogueSearch();
}

function changeSearchPage(delta) {
    const next = GLOBAL_SEARCH_STATE.page + delta;
    if (next < 1 || next > GLOBAL_SEARCH_STATE.totalPages) return;
    GLOBAL_SEARCH_STATE.page = next;
    executeGlobalCatalogueSearch();
}

async function executeGlobalCatalogueSearch() {
    const rawInput = document.getElementById("stockSearchInput")?.value.trim() || "";
    const newsBox = document.getElementById("searchNewsBox");
    const newsText = document.getElementById("searchNewsText");
    const rangeBadge = document.getElementById("searchPageRangeBadge");
    const pagBar = document.getElementById("searchPaginationBar");
    const pageInfo = document.getElementById("searchPageInfo");

    if (!rawInput && GLOBAL_SEARCH_STATE.exchange === "ALL") {
        GLOBAL_SEARCH_STATE = { ...GLOBAL_SEARCH_STATE, query: "", page: 1, totalMatches: 0, totalPages: 1, items: [] };
        newsBox?.classList.add("hidden");
        renderDynamicScanner();
        return;
    }

    GLOBAL_SEARCH_STATE.query = rawInput;
    newsBox?.classList.remove("hidden");
    if (newsText) newsText.textContent = `Searching full NSE/BSE/Crypto/F&O catalogue for "${rawInput || GLOBAL_SEARCH_STATE.exchange}"...`;

    try {
        const res = await secureApiCall("search_stock", {
            method: "GET",
            query: `q=${encodeURIComponent(rawInput)}&exchange=${encodeURIComponent(GLOBAL_SEARCH_STATE.exchange)}&page=${GLOBAL_SEARCH_STATE.page}&pageSize=${GLOBAL_SEARCH_STATE.pageSize}`
        });
        const data = res.ok ? res.data : null;
        if (!data?.success) throw new Error("Search unavailable");

        GLOBAL_SEARCH_STATE.totalMatches = Number(data.totalMatches || 0);
        GLOBAL_SEARCH_STATE.totalPages = Number(data.totalPages || 1);
        const mapped = (data.items || []).map(it => {
            const p = Number(it.price || 0);
            const cur = it.category === "crypto" ? "$" : "₹";
            return {
                ...it,
                price: p,
                priceFormatted: p > 0 ? (p < 1 && it.category === "crypto" ? `$${p.toFixed(4)}` : `${cur}${p.toFixed(2)}`) : "On Chart",
                high: Number(it.high || p),
                low: Number(it.low || p),
                changePct: Number(it.changePct || 0),
                change: it.change || "--",
                rvol: it.rvol !== undefined ? it.rvol : null,
                sector: `${it.exchange} • ${it.instrumentType || "ASSET"}`
            };
        });

        GLOBAL_SEARCH_STATE.items = mapped;
        if (rangeBadge) rangeBadge.textContent = data.pageRange || "0-0 of 0";
        if (newsText) {
            const limitNote = data.providerLimited ? " (Extended via provider fallback)" : "";
            newsText.textContent = mapped.length > 0
                ? `Found ${data.totalMatches} matches across ${data.catalogueSize || mapped.length} catalogue instruments${limitNote}.`
                : `No instrument found for "${rawInput}" in ${GLOBAL_SEARCH_STATE.exchange}.`;
        }
        if (pagBar && pageInfo) {
            pagBar.classList.toggle("hidden", GLOBAL_SEARCH_STATE.totalPages <= 1);
            pageInfo.textContent = `Page ${GLOBAL_SEARCH_STATE.page} / ${GLOBAL_SEARCH_STATE.totalPages}`;
        }

        let resultList = document.getElementById("searchResultsList");
        if (!resultList) {
            resultList = document.createElement("div");
            resultList.id = "searchResultsList";
            resultList.className = "grid grid-cols-1 gap-1.5";
            newsBox?.appendChild(resultList);
        }
        resultList.innerHTML = mapped.slice(0, 12).map((it, index) => `
            <button type="button" data-search-index="${index}" class="search-result-card w-full text-left bg-slate-50 hover:bg-[#00d09c]/10 border border-slate-200 rounded-lg px-2.5 py-2 transition">
                <span class="text-[10px] font-black text-slate-900">${sanitizeCleanText(it.name || it.orderKey)}</span>
                <span class="block text-[8.5px] text-slate-500 mono-font">${sanitizeCleanText(it.symbol)} • ${sanitizeCleanText(it.priceFormatted)}</span>
            </button>
        `).join("");
        resultList.querySelectorAll(".search-result-card").forEach((btn, index) => {
            btn.addEventListener("click", () => openSearchedAssetDetailModal(mapped[index]));
        });
    } catch (e) {
        if (newsText) newsText.textContent = "Catalogue search temporarily unavailable.";
    }
}

function handleRealStockSearch() {
    const rawInput = document.getElementById("stockSearchInput")?.value.trim() || "";
    GLOBAL_SEARCH_STATE.page = 1;
    if (!rawInput && GLOBAL_SEARCH_STATE.exchange === "ALL") {
        clearSearchInput();
        return;
    }
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(executeGlobalCatalogueSearch, 320);
}

async function openSearchedAssetDetailModal(item) {
    if (!item) return;
    const modal = document.getElementById("assetDetailModal");
    if (!modal) return;
    const title = document.getElementById("assetDetailTitle");
    const subtitle = document.getElementById("assetDetailSubtitle");
    if (title) title.textContent = item.name || item.orderKey;
    if (subtitle) subtitle.textContent = `${item.symbol || item.orderKey} • ${item.exchange || ""}`;

    try {
        const res = await secureApiCall("asset_details", {
            method: "GET",
            query: `symbol=${encodeURIComponent(item.symbol || item.orderKey)}&name=${encodeURIComponent(item.name || item.orderKey)}&yahooSymbol=${encodeURIComponent(item.yahooSymbol || "")}&category=${encodeURIComponent(item.category || "")}`
        });
        const data = res.ok ? res.data : null;
        const d = data?.details || {};
        const setText = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        setText("assetDetailOpen", d.open != null ? d.open.toLocaleString("en-IN") : "--");
        setText("assetDetailPrevClose", d.prevClose != null ? d.prevClose.toLocaleString("en-IN") : "--");
        setText("assetDetailDayRange", `${d.dayHigh != null ? d.dayHigh.toLocaleString("en-IN") : "--"} / ${d.dayLow != null ? d.dayLow.toLocaleString("en-IN") : "--"}`);
        setText("assetDetail52Range", `${d.week52High != null ? d.week52High.toLocaleString("en-IN") : "--"} / ${d.week52Low != null ? d.week52Low.toLocaleString("en-IN") : "--"}`);
        setText("assetDetailVolume", d.volume != null ? d.volume.toLocaleString("en-IN") : "--");
        setText("assetDetailSector", d.sector || item.sector || "--");

        const newsEl = document.getElementById("assetDetailNews");
        if (newsEl) {
            newsEl.innerHTML = (data?.news || []).slice(0, 5).map(n => `
                <a href="${sanitizeCleanText(n.link || "#")}" target="_blank" rel="noopener" class="block bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-2 text-slate-700 hover:text-[#059669]">
                    ${sanitizeCleanText(n.title)}
                </a>
            `).join("") || `<p class="text-slate-500">No related news available.</p>`;
        }
    } catch (e) {
        console.warn("[Asset Details]:", e.message);
    }

    const chartBtn = document.getElementById("assetDetailChartBtn");
    const tradeBtn = document.getElementById("assetDetailTradeBtn");
    if (chartBtn) chartBtn.onclick = () => {
        closeAssetDetailModal();
        selectAssetForAnalysis(item.symbol || item.orderKey, item.orderKey);
        switchTab("Chart");
    };
    if (tradeBtn) tradeBtn.onclick = () => {
        closeAssetDetailModal();
        switchTab("Trade");
        const select = document.getElementById("tradeOrderSymbol");
        if (select) {
            const existing = Array.from(select.options).some(o => o.value === item.orderKey);
            if (!existing) {
                const opt = document.createElement("option");
                opt.value = item.orderKey;
                opt.textContent = item.name || item.orderKey;
                select.appendChild(opt);
            }
            select.value = item.orderKey;
            handleOrderSymbolChange();
        }
    };
    modal.classList.remove("hidden");
}

function closeAssetDetailModal() {
    document.getElementById("assetDetailModal")?.classList.add("hidden");
}

function clearSearchInput() {
    const el = document.getElementById("stockSearchInput");
    if (el) el.value = "";
    GLOBAL_SEARCH_STATE = { query: "", exchange: "ALL", page: 1, pageSize: 20, totalMatches: 0, totalPages: 1, items: [] };
    document.getElementById("searchNewsBox")?.classList.add("hidden");
    renderDynamicScanner();
}

function getShikariBadge(engines) {
    if (!engines || !Array.isArray(engines)) return null;
    const shikariEngines = engines.filter(e => e.engineId && e.engineId.startsWith("ENG2"));
    if (!shikariEngines.length) return null;
    
    const badges = [];
    
    // ENG21: Sweep Reclaim
    const sweep = shikariEngines.find(e => e.engineId === "ENG21_SHIKARI_SWEEP_RECLAIM");
    if (sweep && sweep.status === "PASS") {
        const dir = sweep.direction === "BUY" ? "🟢" : "🔴";
        badges.push({ html: `${dir} SWEEP RECLAIM`, class: "bg-[#00d09c]/20 text-[#059669] border-[#00d09c]/30" });
    }
    
    // ENG22: Whale Absorption
    const whale = shikariEngines.find(e => e.engineId === "ENG22_SHIKARI_WHALE_ABSORPTION");
    if (whale && whale.status === "PASS") {
        const dir = whale.direction === "BUY" ? "🟢" : "🔴";
        badges.push({ html: `${dir} WHALE ABSORPTION`, class: "bg-blue-500/20 text-blue-600 border-blue-500/30" });
    }
    
    // ENG23: Dump Shield (WARNING)
    const dump = shikariEngines.find(e => e.engineId === "ENG23_SHIKARI_DUMP_SHIELD");
    if (dump && dump.status === "FAIL") {
        badges.push({ html: "🔴 DUMP SHIELD: VETO", class: "bg-[#eb5b3c]/20 text-[#eb5b3c] border-[#eb5b3c]/30 animate-pulse" });
    } else if (dump && dump.status === "WARN") {
        badges.push({ html: "🟡 DUMP SHIELD: CAUTION", class: "bg-amber-500/20 text-amber-700 border-amber-500/30" });
    }
    
    // ENG24: FII/DII OI Alignment
    const fii = shikariEngines.find(e => e.engineId === "ENG24_SHIKARI_FII_DII_OI");
    if (fii && fii.status === "PASS" && fii.direction !== "NEUTRAL") {
        const dir = fii.direction === "BUY" ? "🟢" : "🔴";
        badges.push({ html: `${dir} FII/DII ALIGNED`, class: "bg-purple-500/20 text-purple-700 border-purple-500/30" });
    } else if (fii && fii.status === "FAIL") {
        badges.push({ html: "🔴 FII/DII CONFLICT", class: "bg-[#eb5b3c]/20 text-[#eb5b3c] border-[#eb5b3c]/30" });
    }
    
    // ENG25: Hunter Unwind (CRITICAL)
    const unwind = shikariEngines.find(e => e.engineId === "ENG25_SHIKARI_HUNTER_UNWIND");
    if (unwind && unwind.status === "FAIL") {
        badges.push({ html: "🚨 HUNTER UNWIND: EXIT!", class: "bg-[#eb5b3c] text-white animate-pulse font-black" });
    }
    
    return badges.length ? badges : null;
}

function renderDynamicScanner(customList = null) {
    const container = document.getElementById("assetsFeedContainer");
    if (!container) return;

    const sourcePool = customList || DYNAMIC_SCAN_CACHE;
    if (!sourcePool || sourcePool.length === 0) {
        container.innerHTML = `<div class="groww-card p-6 text-center space-y-2 bg-white">
            <i class="fa-solid fa-radar animate-spin text-2xl text-[#00d09c]"></i>
            <p class="text-xs font-bold text-slate-800">${customList ? "No matching catalogue assets found." : "Running Baseline NSE/BSE & Crypto Universe Scan..."}</p>
            <p class="text-[10px] text-slate-500 font-mono">${customList ? "Try a different ticker, exchange filter, or ISIN." : "Fetching provider pages & rotating closed-candle deep analysis"}</p>
        </div>`;
        return;
    }

    if (activeCategory === "sectors") {
        const secMap = {};
        sourcePool.forEach(it => {
            const s = it.sector || "Broad Market";
            if (!secMap[s]) secMap[s] = { count: 0, sumChg: 0, topPick: it };
            secMap[s].count++;
            secMap[s].sumChg += Number(it.changePct || 0);
            if (Math.abs(it.changePct) > Math.abs(secMap[s].topPick.changePct)) secMap[s].topPick = it;
        });
        const sectors = Object.entries(secMap)
            .map(([name, d]) => ({ name, avg: d.sumChg / d.count, count: d.count, top: d.topPick }))
            .sort((a, b) => Math.abs(b.avg) - Math.abs(a.avg));

        container.innerHTML = sectors.map(s => {
            const isUp = s.avg >= 0;
            return `<div class="groww-card p-3.5 flex items-center justify-between bg-white">
                <div>
                    <h4 class="text-xs font-black text-slate-900 heading-font">${sanitizeCleanText(s.name)}</h4>
                    <span class="text-[9px] text-slate-500 font-mono">${s.count} Liquid Assets • Leader: ${sanitizeCleanText(s.top.orderKey)}</span>
                </div>
                <span class="text-xs font-mono font-black px-2.5 py-1 rounded-xl ${isUp ? "bg-[#00d09c]/15 text-[#059669]" : "bg-[#eb5b3c]/15 text-[#eb5b3c]"}">${isUp ? "+" : ""}${s.avg.toFixed(2)}%</span>
            </div>`;
        }).join("");
        return;
    }

    let list = [];
    if (activeCategory === "opportunities" || activeCategory === "all") {
        list = sourcePool
            .filter(x => x.opp && x.opp.score >= 80 && (x.opp.direction === "BUY" || x.opp.direction === "SELL"))
            .sort((a, b) => b.opp.score - a.opp.score);

        if (list.length === 0) {
            container.innerHTML = `<div class="groww-card p-6 text-center space-y-2 border border-[#00d09c]/30 bg-white">
                <div class="w-12 h-12 rounded-2xl bg-[#00d09c]/10 text-[#00d09c] flex items-center justify-center mx-auto text-xl"><i class="fa-solid fa-shield-halved"></i></div>
                <h4 class="text-xs font-black text-slate-900 heading-font">No ≥80% Diamond Opportunity Yet</h4>
                <p class="text-[10px] text-slate-600 leading-relaxed">Scanned ${SCANNER_UNIVERSE_COUNT || sourcePool.length} Stocks, Indices &amp; Crypto pairs using 100+ Candlestick &amp; Quant engines. Sub-80% setups are hidden to protect your capital.</p>
                <button onclick="runFullMarketDiamondScan(true)" class="groww-btn-green px-4 py-2 rounded-xl text-[10px] font-black shadow-md active:scale-95 transition">Re-Scan Universe Now</button>
            </div>`;
            return;
        }
    } else if (activeCategory === "gainers") {
        list = [...sourcePool].sort((a, b) => b.changePct - a.changePct).slice(0, 10);
    } else if (activeCategory === "losers") {
        list = [...sourcePool].sort((a, b) => a.changePct - b.changePct).slice(0, 10);
    } else if (activeCategory === "volume") {
        list = [...sourcePool].sort((a, b) => (b.turnover || b.volume) - (a.turnover || a.volume)).slice(0, 5);
    } else if (activeCategory === "volspike") {
        list = [...sourcePool].sort((a, b) => (b.rvol || 0) - (a.rvol || 0)).slice(0, 15);
    } else if (activeCategory === "traps") {
        list = sourcePool.filter(x => x.opp && (x.opp.trapType || (x.opp.patterns || []).some(p => /Breakout|Engulfing|Star|Kicker|Sweep/i.test(p)))).slice(0, 15);
        if (list.length === 0) list = [...sourcePool].sort((a, b) => Math.abs(b.changePct) - Math.abs(a.changePct)).slice(0, 10);
    } else if (activeCategory === "indian") {
        list = sourcePool.filter(x => x.category === "indian").slice(0, 30);
    } else if (activeCategory === "crypto") {
        list = sourcePool.filter(x => x.category === "crypto" || x.category === "commodity").slice(0, 30);
    } else {
        list = sourcePool.slice(0, 20);
    }

    const headerStrip = `<div class="flex items-center justify-between px-1 text-[9.5px] font-mono text-slate-500">
        <span>${customList ? "Catalogue Matches" : `Baseline Scanned: <b class="text-slate-900">${SCANNER_COVERAGE.baselineScanned || sourcePool.length}</b> (Deep: ${SCANNER_COVERAGE.deepAnalyzedCount || 0})`}</span>
        <span>Showing <b class="text-[#00d09c]">${list.length}</b> Items</span>
    </div>`;

    container.innerHTML = headerStrip + list.map(a => {
        const opp = a.opp || null;
        const hasStrongOpp = opp && opp.score >= 80 && (opp.direction === "BUY" || opp.direction === "SELL");
        const isBuy = hasStrongOpp ? opp.direction === "BUY" : a.changePct >= 0;
        const cardPulse = hasStrongOpp ? (isBuy ? "radar-blink-buy border-2 border-[#00d09c]" : "radar-blink-sell border-2 border-[#eb5b3c]") : "";
        const rvolTxt = (a.rvol !== null && a.rvol !== undefined) ? `RVOL ${a.rvol}x` : "RVOL N/A";
        const badgeText = hasStrongOpp
            ? `${isBuy ? "🟢" : "🔴"} HEURISTIC ${opp.score} • ${opp.optionHint}`
            : `${a.dataQuality || "LIVE"} • ${rvolTxt}`;
        const badgeCls = hasStrongOpp
            ? (isBuy ? "bg-[#00d09c] text-white font-black" : "bg-[#eb5b3c] text-white font-black")
            : "bg-slate-100 text-slate-600 border border-slate-200 font-bold";

        const autoSl = hasStrongOpp ? opp.sl : Number((isBuy ? a.price * 0.985 : a.price * 1.015).toFixed(2));
        const autoTg = hasStrongOpp ? opp.target : Number((isBuy ? a.price * 1.03 : a.price * 0.97).toFixed(2));
        const sideCmd = isBuy ? "BUY" : "SELL";

        return `
        <div id="assetCard_${a.id}" class="groww-card p-3.5 space-y-2.5 transition bg-white ${cardPulse}">
            <div class="flex items-center justify-between cursor-pointer" onclick="selectAssetForAnalysis('${sanitizeCleanText(a.symbol)}', '${sanitizeCleanText(a.orderKey)}'); switchTab('Chart');">
                <div class="flex items-center space-x-2.5">
                    <span class="w-9 h-9 rounded-xl bg-slate-100 border border-slate-200 flex items-center justify-center font-black ${isBuy ? "text-[#00d09c]" : "text-[#eb5b3c]"} text-xs shadow-inner">${sanitizeCleanText(a.orderKey.slice(0, 3))}</span>
                    <div>
                        <div class="flex items-center space-x-1.5">
                            <h4 class="font-extrabold text-xs text-slate-900 leading-tight heading-font">${sanitizeCleanText(a.name)}</h4>
                            <span class="text-[8px] bg-slate-100 text-slate-600 px-1.5 py-0.2 rounded font-mono font-bold border border-slate-200">${sanitizeCleanText(a.exchange)}</span>
                        </div>
                        <span class="text-[9px] text-slate-500 font-mono">${sanitizeCleanText(a.sector || a.category.toUpperCase())}</span>
                    </div>
                </div>
                <div class="text-right">
                    <div class="text-xs font-black text-slate-900 mono-font">${sanitizeCleanText(a.priceFormatted)}</div>
                    <span class="text-[9.5px] font-mono font-bold ${a.changePct >= 0 ? "text-[#00d09c] bg-[#00d09c]/10 border-[#00d09c]/25" : "text-[#eb5b3c] bg-[#eb5b3c]/10 border-[#eb5b3c]/25"} px-2 py-0.5 rounded-md border">${sanitizeCleanText(a.change)}</span>
                </div>
            </div>

            <div class="flex items-center justify-between">
                <span class="text-[8.5px] font-mono px-2 py-0.5 rounded-lg ${badgeCls}">${badgeText}</span>
                ${opp?.rrRatio && opp.rrRatio !== "--" ? `<span class="text-[9px] font-mono font-bold text-slate-600">R:R <b class="text-slate-900">${opp.rrRatio}</b></span>` : ""}
            </div>

            ${opp && opp.reasons && opp.reasons.length > 0 ? `<div class="text-[9px] font-mono text-slate-700 bg-slate-50 px-2.5 py-1.5 rounded-xl border border-slate-200 truncate">✨ ${sanitizeCleanText(opp.reasons.join(" • "))}</div>` : ""}

            ${(opp && opp.engines && getShikariBadge(opp.engines)) ? `
            <div class="flex flex-wrap gap-1.5">
                ${getShikariBadge(opp.engines).map(b => `<span class="text-[7.5px] font-mono font-bold px-2 py-0.5 rounded-lg border ${b.class}">${b.html}</span>`).join("")}
            </div>` : ""}

            <div class="grid grid-cols-3 gap-1.5 bg-slate-50 p-2 rounded-xl border border-slate-200 mono-font text-[9.5px] text-center">
                <div><span class="text-slate-500 text-[8px] block">Ref Entry</span><b class="text-slate-900">${hasStrongOpp ? opp.entry : a.price}</b></div>
                <div class="border-l border-slate-200"><span class="text-slate-500 text-[8px] block">Auto SL</span><b class="text-[#eb5b3c]">${autoSl}</b></div>
                <div class="border-l border-slate-200"><span class="text-slate-500 text-[8px] block">Auto TG</span><b class="text-[#00d09c]">${autoTg}</b></div>
            </div>

            <div class="flex items-center justify-between gap-2 pt-0.5">
                <button onclick="selectAssetForAnalysis('${sanitizeCleanText(a.symbol)}', '${sanitizeCleanText(a.orderKey)}'); switchTab('Chart');" class="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-800 text-[9.5px] font-mono font-bold py-1.5 rounded-xl border border-slate-200 transition">
                    <i class="fa-solid fa-chart-candlestick text-[#00d09c] mr-1"></i> Live Chart
                </button>
                <button onclick="autoFillOpportunityToDesk('${sanitizeCleanText(a.orderKey)}', '${sideCmd}', ${autoSl}, ${autoTg}, '${sanitizeCleanText(a.symbol)}')" class="flex-1 ${isBuy ? "groww-btn-green" : "groww-btn-red"} text-[9.5px] font-mono font-black py-1.5 rounded-xl transition shadow-sm">
                    <i class="fa-solid fa-bolt mr-1"></i> Trade (${sideCmd})
                </button>
            </div>
        </div>`;
    }).join("");
}

function renderAssets(customList) {
    renderDynamicScanner(customList);
}

function filterCategory(cat) {
    activeCategory = cat === "all" ? "opportunities" : cat;
    ["opportunities", "gainers", "losers", "volume", "volspike", "traps", "sectors", "indian", "crypto"].forEach(c => {
        const btn = document.getElementById(`catBtn_${c}`);
        if (btn) btn.className = c === activeCategory
            ? "px-3.5 py-1.5 rounded-xl bg-[#00d09c] text-white font-black shadow-md shrink-0 transition"
            : "px-3.5 py-1.5 rounded-xl bg-white border border-slate-200 text-slate-600 shadow-sm shrink-0 transition";
    });
    renderDynamicScanner();
}

async function openDetailedInstitutionalAnalysis(assetKey) {
    if (!checkProAccess()) return;
    const asset = MASTER_ASSETS.find(a => a.id === assetKey) || MASTER_ASSETS[0];
    currentSelectedAsset = asset;
    const sr = calculateDynamicSupportResistance(asset.high, asset.low, asset.priceNum);
    const delta = await fetchRealOrderflowDelta(asset);
    const qData = await runQuantOrchestrator(asset.id);
    const tw = qData?.twoWay;

    document.getElementById("modalAssetTitle").textContent = `${asset.name} (${asset.dataEnvelope?.quality || "OFFLINE"})`;
    document.getElementById("modalAssetPrice").textContent = `${asset.price} • Source: ${asset.dataEnvelope?.source || "NONE"}`;
    document.getElementById("modalTodayHigh").textContent = asset.highFormatted || "-";
    document.getElementById("modalTodayLow").textContent = asset.lowFormatted || "-";
    document.getElementById("modalSupportLevel").textContent = sr.s1;
    document.getElementById("modalResistanceLevel").textContent = sr.r1;

    document.getElementById("modalEntry").textContent = tw?.entry || asset.price;
    document.getElementById("modalTarget").textContent = tw?.target || sr.r1;
    document.getElementById("modalSl").textContent = tw?.sl || sr.s1;

    document.getElementById("modalWhatText").textContent = asset.priceNum > 0
        ? `${asset.name} LTP ${asset.price}. Two-Way Setup: ${tw?.direction || "WAIT"} (${tw?.optionHint || "-"}) • R:R ${tw?.rrRatio || "--"}. Depth (${delta.marketType || "NONE"}): ${delta.status === "LIVE" ? `${delta.buyers}% Bid / ${delta.sellers}% Ask` : "Unavailable"}.`
        : "Feed offline — waiting for verified market tick.";
    document.getElementById("modalWhyText").textContent = `Regime: ${qData?.regime?.regime || "UNKNOWN"} | EMA(9/21): ${qData?.ema9 ?? "--"}/${qData?.ema21 ?? "--"} | RSI(14): ${qData?.rsi14 ?? "--"} | ATR: ${qData?.volatility?.atr_value ?? "UNAVAILABLE"} (${qData?.volatility?.realized_volatility ?? "--"}% Vol) | VWAP: ${qData?.volIntel?.vwap ?? "UNAVAILABLE"}.`;
    document.getElementById("modalHowText").textContent = `Reasons: ${tw?.reasons?.join(", ") || "Waiting"} | Invalidation: ${tw?.invalidation || "Strict SL mandatory"} | Event Risk: ${qData?.eventRisk?.riskLevel || "UNKNOWN"}.`;

    document.getElementById("analysisModal")?.classList.remove("hidden");
}

function closeAnalysisModal() { document.getElementById("analysisModal")?.classList.add("hidden"); }

function setCryptoLeverage(lev) {
    const sym = document.getElementById("tradeOrderSymbol")?.value;
    const maxCap = Math.min(10, CANONICAL_INSTRUMENTS[sym]?.maxLeverage || 10);
    lev = Math.min(maxCap, Math.max(1, lev || 1));
    selectedCryptoLeverage = lev;
    [1, 2, 3, 5, 10].forEach(l => {
        const btn = document.getElementById(`btnLev_${l}`);
        if (btn) btn.className = (l === lev)
            ? (lev <= 2 ? "py-1.5 rounded-lg bg-[#00d09c] text-white font-black shadow-sm transition" : "py-1.5 rounded-lg bg-amber-500 text-white font-black shadow-sm transition")
            : "py-1.5 rounded-lg bg-slate-100 text-slate-600 border border-slate-200 transition";
    });
    const disp = document.getElementById("activeLeverageDisplay");
    if (disp) {
        disp.textContent = `${lev}X LEVERAGE${lev === 1 ? " (SAFE)" : ""}`;
        disp.className = lev <= 2
            ? "text-[9px] font-black text-[#059669] bg-[#00d09c]/15 px-2 py-0.5 rounded border border-[#00d09c]/30"
            : "text-[9px] font-black text-amber-600 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/30";
    }
    const customInp = document.getElementById("customLeverageInput");
    if (customInp) {
        customInp.max = String(maxCap);
        if (parseInt(customInp.value, 10) !== lev) customInp.value = lev;
    }
    updateCryptoMarginCalculation();
}

function updateCryptoMarginCalculation() {
    const symbol = document.getElementById("tradeOrderSymbol")?.value;
    const box = document.getElementById("cryptoLeverageControlBox");
    if (!box) return;
    const isCrypto = symbol && symbol.includes("USDT");
    if (!isCrypto) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");

    const asset = MASTER_ASSETS.find(a => a.orderKey === symbol) || (typeof DYNAMIC_SCAN_CACHE !== "undefined" ? DYNAMIC_SCAN_CACHE.find(a => a.orderKey === symbol) : null);
    const livePrice = asset?.priceNum || asset?.price || 0;
    const qual = asset?.dataEnvelope?.quality || (livePrice > 0 ? "LIVE" : "OFFLINE");

    if (!Number.isFinite(livePrice) || livePrice <= 0 || qual === "OFFLINE" || qual === "STALE") {
        document.getElementById("calcPositionValue").textContent = `Unavailable (${qual} feed)`;
        document.getElementById("calcRequiredMargin").textContent = "--";
        document.getElementById("calcEstLiqPrice").textContent = "--";
        document.getElementById("calcMarginSufficiency").textContent = `Feed ${qual}`;
        return;
    }

    const qty = Number(document.getElementById("tradeOrderQty")?.value) || 0;
    const positionValue = livePrice * qty;
    const requiredMargin = selectedCryptoLeverage > 0 ? (positionValue / selectedCryptoLeverage) : positionValue;
    const estLiq = livePrice * (1 - (1 / selectedCryptoLeverage) * 0.9);

    document.getElementById("calcPositionValue").textContent = `$${positionValue.toFixed(2)} USDT`;
    document.getElementById("calcRequiredMargin").textContent = `$${requiredMargin.toFixed(2)} USDT`;
    document.getElementById("calcEstLiqPrice").textContent = `$${estLiq.toFixed(2)} (Est.)`;

    const usdtBal = Number(SESSION_VAULT.coindcxUsdtNum) || parseFloat(document.getElementById("hudCoinDcxUsdtVal")?.textContent || "") || 0;
    const elSuff = document.getElementById("calcMarginSufficiency");
    if (elSuff) {
        if (usdtBal > 0 && requiredMargin > usdtBal) {
            elSuff.className = "text-[#eb5b3c] font-bold";
            elSuff.textContent = `Margin LOW ($${usdtBal.toFixed(2)})`;
        } else {
            elSuff.className = "text-[#00d09c] font-bold";
            elSuff.textContent = "Margin OK";
        }
    }
    calculateSafeQtySuggestion();
}

function switchPositionBrokerTab(broker) {
    activePositionBroker = broker;
    const bShoonya = document.getElementById("posBrokerBtn_shoonya");
    const bCoindcx = document.getElementById("posBrokerBtn_coindcx");
    const titleLabel = document.getElementById("posBrokerTitleLabel");
    const twoWayCard = document.getElementById("twoWayExecutionDeskCard");
    const coindcxWalletCard = document.getElementById("coindcxDepositWalletCard");

    if (broker === "shoonya") {
        if (bShoonya) bShoonya.className = "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm";
        if (bCoindcx) bCoindcx.className = "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
        if (titleLabel) titleLabel.textContent = "Shoonya Balance / Limits";
        twoWayCard?.classList.remove("hidden");
        coindcxWalletCard?.classList.add("hidden");
    } else {
        if (bCoindcx) bCoindcx.className = "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm";
        if (bShoonya) bShoonya.className = "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
        if (titleLabel) titleLabel.textContent = "CoinDCX Futures Balance";
        twoWayCard?.classList.add("hidden");
        coindcxWalletCard?.classList.remove("hidden");
    }
    syncRealBrokerBalances();
    initSyncPositionsToMemory();
    syncBrokerHoldingsAndOrders();
}

function switchPositionSubTab(sub) {
    activePositionSubTab = sub;
    ["open", "pending", "history"].forEach(s => {
        const btn = document.getElementById(`posSubTab_${s}`);
        const box = document.getElementById(`positionSubContent_${s}`);
        if (btn) btn.className = s === sub ? "flex-1 py-1 rounded-lg bg-white text-slate-900 font-bold shadow-sm" : "flex-1 py-1 rounded-lg text-slate-600 hover:text-slate-900";
        if (box) {
            if (s === sub) box.classList.remove("hidden");
            else box.classList.add("hidden");
        }
    });
}

function switchHoldingsBrokerTab(broker) {
    activeHoldingsBroker = broker;
    const bShoonya = document.getElementById("holdingsBrokerBtn_shoonya");
    const bCoindcx = document.getElementById("holdingsBrokerBtn_coindcx");
    const titleLabel = document.getElementById("holdingsListTitle");

    if (broker === "shoonya") {
        if (bShoonya) bShoonya.className = "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm";
        if (bCoindcx) bCoindcx.className = "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
        if (titleLabel) titleLabel.textContent = "Shoonya Delivery Assets";
    } else {
        if (bCoindcx) bCoindcx.className = "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm";
        if (bShoonya) bShoonya.className = "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
        if (titleLabel) titleLabel.textContent = "CoinDCX Spot Holdings";
    }
    renderHoldingsItems();
}

function toggleHoldingsValuesVisibility() {
    holdingsValuesVisible = !holdingsValuesVisible;
    const icon = document.getElementById("holdingEyeIcon");
    if (icon) icon.className = holdingsValuesVisible ? "fa-solid fa-eye text-xs" : "fa-solid fa-eye-slash text-xs";
    renderHoldingsItems();
}

function renderHoldingsItems() {
    const container = document.getElementById("holdingsItemsList");
    const valEl = document.getElementById("holdingsTotalVal");
    const invEl = document.getElementById("holdingsInvestedVal");
    const ret1dEl = document.getElementById("holdings1dRet");
    const retTotEl = document.getElementById("holdingsTotalRet");
    if (!container) return;

    const list = activeHoldingsBroker === "shoonya" ? SESSION_VAULT.shoonyaHoldingsList : SESSION_VAULT.coindcxHoldingsList;

    let totVal = 0, totInv = 0, totRet = 0;
    list.forEach(h => {
        totVal += (h.currentVal || 0);
        totInv += (h.investedVal || 0);
        totRet += (h.totalReturnVal || 0);
    });

    const mask = (str) => holdingsValuesVisible ? str : "••••••";
    if (valEl) valEl.textContent = mask(`₹${totVal.toFixed(2)}`);
    if (invEl) invEl.textContent = mask(`₹${totInv.toFixed(2)}`);
    if (retTotEl) {
        const pct = totInv > 0 ? ((totRet / totInv) * 100).toFixed(2) : "0.00";
        retTotEl.textContent = mask(`₹${totRet >= 0 ? "+" : ""}${totRet.toFixed(2)} (${pct}%)`);
        retTotEl.className = totRet >= 0 ? "text-[#00d09c]" : "text-[#eb5b3c]";
    }
    if (ret1dEl) ret1dEl.textContent = mask("₹0.00 (0.00%)");

    if (list.length === 0) {
        container.innerHTML = `<p class="text-[9px] text-slate-500 font-mono text-center py-4">No ${activeHoldingsBroker.toUpperCase()} holdings found.</p>`;
        return;
    }

    container.innerHTML = list.map(h => `
        <div class="flex items-center justify-between p-3 rounded-2xl bg-slate-50 border border-slate-200 text-xs">
            <div>
                <h5 class="font-bold text-slate-950">${sanitizeCleanText(h.symbol)}</h5>
                <span class="text-[9px] text-slate-500 font-mono">${h.qty} shares • Avg ₹${h.avgPrice}</span>
            </div>
            <div class="text-right">
                <div class="font-black text-slate-950 mono-font">${mask(`₹${(h.currentVal || 0).toFixed(2)}`)}</div>
                <div class="text-[9px] font-mono ${(h.totalReturnVal || 0) >= 0 ? "text-[#00d09c]" : "text-[#eb5b3c]"} font-bold">
                    ${mask(`${(h.totalReturnVal || 0) >= 0 ? "+" : ""}${((h.totalReturnVal || 0) / (h.investedVal || 1) * 100).toFixed(2)}%`)}
                </div>
            </div>
        </div>
    `).join("");
}

function renderOpenPositions() {
    const container = document.getElementById("openPositionsList");
    if (!container) return;
    container.innerHTML = "";

    const uniquePositions = [];
    const seen = new Set();
    for (const k in SESSION_VAULT.fastPositionsMap) {
        const p = SESSION_VAULT.fastPositionsMap[k];
        if (p && !seen.has(p.id) && Number(p.qty) !== 0) {
            if (activePositionBroker === "shoonya" && p.broker !== "SHOONYA") continue;
            if (activePositionBroker === "coindcx" && p.broker !== "COINDCX") continue;
            seen.add(p.id);
            uniquePositions.push(p);
        }
    }

    if (uniquePositions.length === 0) {
        const empty = document.createElement("p");
        empty.className = "text-[9px] text-slate-500 font-mono text-center py-2";
        empty.textContent = `No active open ${activePositionBroker.toUpperCase()} positions.`;
        container.appendChild(empty);
        return;
    }

    uniquePositions.forEach(p => {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between p-2.5 rounded-xl bg-slate-50 border border-slate-200 text-[10px] mono-font";

        const left = document.createElement("div");
        const top = document.createElement("div");
        top.className = "flex items-center space-x-1.5";
        const dirBadge = document.createElement("span");
        dirBadge.className = `px-1.5 py-0.2 rounded text-[8px] font-black ${p.qty > 0 ? "bg-[#00d09c]/20 text-[#059669]" : "bg-[#eb5b3c]/20 text-[#eb5b3c]"}`;
        dirBadge.textContent = p.qty > 0 ? "LONG" : "SHORT";
        const symBold = document.createElement("b");
        symBold.className = "text-slate-900";
        symBold.textContent = p.tsym || p.token;
        top.append(dirBadge, symBold);

        // SHIKARI Footprint Badge for live positions
        const shikariLiveBadge = document.createElement("span");
        shikariLiveBadge.id = `shikari-live-${p.id}`;
        shikariLiveBadge.className = "flex flex-wrap gap-1 ml-2";
        top.append(shikariLiveBadge);

        const sub = document.createElement("span");
        sub.className = "text-[8.5px] text-slate-500 block mt-0.5";
        const pnlVal = (p.liveMtm !== undefined ? p.liveMtm : (p.upnl || 0)) + (p.rpnl || 0);
        const curPrefix = p.quoteCurrency === "USDT" ? "$" : "₹";
        sub.textContent = `Qty: ${Math.abs(p.qty)} @ ${p.avgPrice} • PnL: ${pnlVal >= 0 ? "+" : ""}${curPrefix}${pnlVal.toFixed(2)}`;
        left.append(top, sub);

        const exitBtn = document.createElement("button");
        exitBtn.className = "px-2.5 py-1.5 rounded-lg bg-[#eb5b3c] hover:bg-rose-600 text-white font-black text-[9px] active:scale-95 transition shadow-sm";
        exitBtn.textContent = "FAST EXIT";
        exitBtn.addEventListener("click", () => exitOpenPosition(p.id));

        row.append(left, exitBtn);
        container.appendChild(row);
    });
}

function renderPaperAutomationPositions() {
    const container = document.getElementById("paperAutomationPositionsList");
    if (!container) return;
    container.innerHTML = "";
    const positions = SESSION_VAULT.paperAutomationPositions.filter(position => position.status === "OPEN");
    if (!positions.length) {
        const empty = document.createElement("p");
        empty.className = "text-[9px] text-slate-500 font-mono text-center py-2";
        empty.textContent = "No open auto-trade paper positions.";
        container.appendChild(empty);
        return;
    }
    positions.forEach(position => {
        const row = document.createElement("div");
        const isAuto = position.isAutoTrade === true;
        row.className = `flex flex-col gap-1.5 p-2.5 rounded-xl ${isAuto ? "bg-green-50 border border-[#00d09c]" : "bg-slate-50 border border-slate-200"} text-[10px] mono-font`;
        
        // Header with type badge, symbol, and AI Swarm status
        const header = document.createElement("div");
        header.className = "flex items-center justify-between flex-wrap gap-1.5";
        const badge = document.createElement("span");
        badge.className = `px-1.5 py-0.5 rounded text-[8px] font-black ${isAuto ? "bg-[#00d09c]/15 text-[#059669]" : "bg-slate-200 text-slate-600"}`;
        badge.textContent = isAuto ? "🤖 AUTO" : "📝 MANUAL";
        const symbol = document.createElement("span");
        symbol.className = "text-slate-700 font-bold";
        symbol.textContent = `${position.side} ${position.symbol} @ ${position.entry}`;
        
        // AI Swarm Telemetry Badge
        const swarmBadge = document.createElement("span");
        swarmBadge.id = `swarm-badge-${position.id}`;
        swarmBadge.className = "px-2 py-0.5 rounded text-[7px] font-black bg-gradient-to-r from-purple-600 to-blue-600 text-white shadow-sm flex items-center gap-1";
        swarmBadge.innerHTML = `<span>🧠</span><span>100 AI Agents Supervising (360°)</span><span id="swarm-consensus-${position.id}">Consensus: --%</span>`;
        
        // SHIKARI Footprint Badges
        const shikariBadgeContainer = document.createElement("div");
        shikariBadgeContainer.id = `shikari-badge-${position.id}`;
        shikariBadgeContainer.className = "flex flex-wrap gap-1";
        
        header.append(badge, symbol, swarmBadge, shikariBadgeContainer);
        
        // Details: Qty, SL, TP, Trailing, Favorable, P&L
        const details = document.createElement("div");
        details.className = "flex flex-wrap items-center gap-2 text-[8px] text-slate-500 font-mono";
        const pnl = (position.currentPrice && position.entry) 
            ? (position.side === "BUY" ? (position.currentPrice - position.entry) : (position.entry - position.currentPrice)) * position.quantity
            : 0;
        const pnlColor = pnl >= 0 ? "text-[#059669]" : "text-[#eb5b3c]";
        const trailClass = "text-blue-600 font-bold";
        const favClass = "text-purple-600 font-bold";
        details.innerHTML = `
            <span>Qty: ${position.quantity}</span>
            <span>Entry: ${position.entry}</span>
            <span class="text-[#eb5b3c]">SL: ${position.stopLoss}</span>
            <span class="text-[#00d09c]">TP: ${position.takeProfit}</span>
            ${position.trailingDistance ? `<span class="${trailClass}">Trail: ${position.trailingDistance}</span>` : ""}
            ${position.favorablePrice ? `<span class="${favClass}">Fav: ${position.favorablePrice}</span>` : ""}
            <span class="${pnlColor}">P&L: ${pnl.toFixed(2)}</span>
        `;
        
        // Exit button
        const exit = document.createElement("button");
        exit.type = "button";
        exit.className = "px-2.5 py-1.5 rounded-lg bg-[#eb5b3c] text-white font-black text-[9px] mt-1.5 w-full";
        exit.textContent = "FAST EXIT";
        exit.addEventListener("click", () => fastExitPaperPosition(position.id));
        
        row.append(header, details, exit);
        container.appendChild(row);
    });
}

async function syncAutomationPaperPositions() {
    if (!auth.currentUser) return;
    const res = await fastApiRequest("/automation/positions");
    if (!res.ok || !Array.isArray(res.data?.positions)) {
        console.error("Could not load persisted paper positions", res.data?.detail || res.status);
        return;
    }
    SESSION_VAULT.paperAutomationPositions = res.data.positions;
    // Fetch current prices for P&L
    updatePaperPositionsPricesFromFeed();
    renderPaperAutomationPositions();
}

function updatePaperPositionsPricesFromFeed() {
    const openPositions = SESSION_VAULT.paperAutomationPositions.filter(p => p.status === "OPEN");
    if (!openPositions.length) return;
    
    // Use MASTER_ASSETS which has real-time prices from market feed
    openPositions.forEach(pos => {
        const asset = MASTER_ASSETS.find(a => 
            (pos.exchange === "COINDCX" && a.symbol === pos.symbol) ||
            (pos.exchange !== "COINDCX" && a.symbol === pos.symbol) ||
            (pos.orderKey && a.orderKey === pos.orderKey)
        );
        if (asset && asset.priceNum > 0) {
            pos.currentPrice = asset.priceNum;
        }
    });
}

async function startPaperPnLPolling() {
    if (window.paperPnLPollingInterval) return;
    window.paperPnLPollingInterval = setInterval(async () => {
        if (!auth.currentUser || document.hidden) return;
        const activeTab = document.getElementById("subTabContent_autotrade");
        if (activeTab && !activeTab.classList.contains("hidden")) {
            updatePaperPositionsPricesFromFeed();
            renderPaperAutomationPositions();
        }
    }, 3000); // 3 second polling
}

function stopPaperPnLPolling() {
    if (window.paperPnLPollingInterval) {
        clearInterval(window.paperPnLPollingInterval);
        window.paperPnLPollingInterval = null;
    }
}

async function fastExitPaperPosition(positionId, skipConfirmation = false) {
    const position = SESSION_VAULT.paperAutomationPositions.find(item => item.id === positionId);
    if (!position || position.status !== "OPEN") return;
    if (!skipConfirmation && !confirm(`FAST EXIT\n\nClose ${position.side} ${position.symbol} paper position now?`)) return;
    const res = await fastApiRequest(`/automation/positions/${encodeURIComponent(positionId)}/exit`, {
        method: "POST"
    });
    if (!res.ok || !res.data?.success) {
        alert(res.data?.detail || "Paper-position fast exit failed.");
        return;
    }
    await Promise.allSettled([syncAutomationPaperPositions(), syncServerRiskState()]);
}

async function updateAISwarmTelemetry() {
    if (!auth.currentUser) return;
    try {
        const res = await fastApiRequest("/ai_swarm/status");
        if (!res.ok || !res.data?.swarms) return;
        
        res.data.swarms.forEach(swarm => {
            const badge = document.getElementById(`swarm-badge-${swarm.order_id}`);
            const consensusEl = document.getElementById(`swarm-consensus-${swarm.order_id}`);
            if (!badge || !consensusEl) return;
            
            const consensusPct = Math.round(swarm.consensus_score * 100);
            consensusEl.textContent = `Consensus: ${consensusPct}%`;
            
            if (swarm.is_terminated) {
                badge.className = "px-2 py-0.5 rounded text-[7px] font-black bg-slate-500 text-white flex items-center gap-1";
                badge.innerHTML = `<span>⏹️</span><span>Swarm Terminated</span><span>${swarm.last_verdict}</span>`;
            } else if (consensusPct >= 100) {
                badge.className = "px-2 py-0.5 rounded text-[7px] font-black bg-[#eb5b3c] text-white animate-pulse flex items-center gap-1";
                badge.innerHTML = `<span>🚨</span><span>100% CONSENSUS - EXITING!</span><span>${swarm.last_verdict}</span>`;
            } else if (consensusPct >= 80) {
                badge.className = "px-2 py-0.5 rounded text-[7px] font-black bg-amber-500 text-white flex items-center gap-1";
                badge.innerHTML = `<span>⚠️</span><span>High Consensus: ${consensusPct}%</span><span>${swarm.last_verdict}</span>`;
            } else if (consensusPct >= 50) {
                badge.className = "px-2 py-0.5 rounded text-[7px] font-black bg-blue-500 text-white flex items-center gap-1";
                badge.innerHTML = `<span>🧠</span><span>Moderate Consensus: ${consensusPct}%</span><span>${swarm.last_verdict}</span>`;
            } else {
                badge.className = "px-2 py-0.5 rounded text-[7px] font-black bg-gradient-to-r from-purple-600 to-blue-600 text-white flex items-center gap-1";
                badge.innerHTML = `<span>🧠</span><span>100 AI Agents Supervising (360°)</span><span>Consensus: ${consensusPct}%</span>`;
            }
        });
    } catch (e) {
        console.warn("[AI Swarm Telemetry]", e);
    }
}

async function updateShikariTelemetry() {
    if (!auth.currentUser) return;
    try {
        const res = await fastApiRequest("/engines/evaluate", {
            method: "POST",
            jsonBody: {
                symbol: currentSelectedAsset?.symbol || "NSE:NIFTY",
                order_key: currentSelectedAsset?.orderKey || "NIFTY",
                category: currentSelectedAsset?.category || "indian",
                ltp: currentSelectedAsset?.priceNum || 0,
                data_source: "LIVE",
                data_quality: "LIVE",
            }
        });
        if (!res.ok || !res.data?.engines) return;
        
        const engines = res.data.engines;
        const shikariBadges = getShikariBadge(engines);
        if (!shikariBadges || !shikariBadges.length) return;
        
        const badgeHtml = shikariBadges.map(b => 
            `<span class="text-[7px] font-mono font-bold px-2 py-0.5 rounded-lg border ${b.class}">${b.html}</span>`
        ).join("");
        
        // Update all visible paper position Shikari badges
        SESSION_VAULT.paperAutomationPositions
            .filter(p => p.status === "OPEN")
            .forEach(position => {
                const container = document.getElementById(`shikari-badge-${position.id}`);
                if (container) container.innerHTML = badgeHtml;
            });
        
        // Update live position Shikari badges
        Object.values(SESSION_VAULT.fastPositionsMap).forEach(p => {
            if (p && Number(p.qty) !== 0) {
                const container = document.getElementById(`shikari-live-${p.id}`);
                if (container) container.innerHTML = badgeHtml;
            }
        });
    } catch (e) {
        console.warn("[SHIKARI Telemetry]", e);
    }
}

async function startAISwarmTelemetryPolling() {
    if (window.aiSwarmTelemetryInterval) return;
    window.aiSwarmTelemetryInterval = setInterval(async () => {
        if (!auth.currentUser || document.hidden) return;
        const activeTab = document.getElementById("subTabContent_autotrade");
        if (activeTab && !activeTab.classList.contains("hidden")) {
            await updateAISwarmTelemetry();
            await updateShikariTelemetry();
        }
    }, 1000); // 1 second polling for real-time swarm status
}

function stopAISwarmTelemetryPolling() {
    if (window.aiSwarmTelemetryInterval) {
        clearInterval(window.aiSwarmTelemetryInterval);
        window.aiSwarmTelemetryInterval = null;
    }
}

async function panicExitAllPositions() {
    const positions = [];
    const seen = new Set();
    Object.values(SESSION_VAULT.fastPositionsMap).forEach(position => {
        if (position && Number(position.qty) !== 0 && !seen.has(position.id)) {
            seen.add(position.id);
            positions.push(position);
        }
    });
    const paperPositions = SESSION_VAULT.paperAutomationPositions.filter(position => position.status === "OPEN");
    if (!positions.length && !paperPositions.length) {
        alert("No open positions were found.");
        return;
    }
    if (!confirm(`KILL ALL / PANIC EXIT\n\nAttempt fast exit for ${positions.length + paperPositions.length} open position(s)?`)) return;
    for (const position of positions) {
        await exitOpenPosition(position.id, true);
    }
    for (const position of paperPositions) {
        await fastExitPaperPosition(position.id, true);
    }
}

async function exitOpenPosition(posId, skipConfirmation = false) {
    if (isOrderDispatching) return;
    const pos = SESSION_VAULT.fastPositionsMap[posId];
    if (!pos || Number(pos.qty) === 0) return;

    const tsymUpper = String(pos.tsym || pos.token || "").toUpperCase();
    const orderKey = matchCanonicalOrderKeyFromTsym(tsymUpper) || tsymUpper.replace(/-EQ$|-FUT$/i, "");
    const exitSide = pos.qty > 0 ? "SELL" : "BUY";
    const exitQty = Math.abs(pos.qty);
    const mode = LIVE_AUTO_TRADING_ENABLED ? "LIVE" : "PAPER";

    if (!skipConfirmation) {
        const ok = confirm(`[PROTECTED FAST EXIT (E84)]\n1. Cancel pending SL/TP orders\n2. Re-read net position & close remaining qty\n3. Verify net position = 0\n\nAsset: ${orderKey} (${pos.broker})\nSide: ${exitSide} | Qty: ${exitQty}\n— Confirm Fast Exit?`);
        if (!ok) return;
    }

    isOrderDispatching = true;
    const statusBox = document.getElementById("orderStatusMsg");
    if (statusBox) {
        statusBox.className = "p-2 rounded-xl text-[10px] mono-font font-bold bg-amber-500/15 border border-amber-500/40 text-amber-700 block";
        statusBox.textContent = `Executing Fast Exit (Cancelling protection & verifying net zero)...`;
    }

    try {
        const clientOrderId = `FEXIT_${Date.now()}_${crypto.getRandomValues(new Uint32Array(1))[0]}`;
        const res = await secureApiCall("fast_exit", {
            method: "POST",
            headers: { "X-Client-Order-Id": clientOrderId },
            jsonBody: { symbol: orderKey, exchange: pos.exchange, side: exitSide, qty: exitQty, mode }
        });

        if (res.ok && res.data?.success) {
            recordAndSyncTradeLog(orderKey, exitSide, res.data.closedQty || exitQty, pos.broker, res.data.state, res.data.brokerOrderId || clientOrderId);
            if (statusBox) {
                statusBox.className = "p-2 rounded-xl text-[10px] mono-font font-bold bg-[#00d09c]/15 border border-[#00d09c]/40 text-[#059669] block";
                statusBox.textContent = `${res.data.state} • Cancelled SL/TP: ${res.data.cancelledOrdersCount || 0} • Net Zero Verified: ${res.data.netZeroVerified ? "YES" : "PENDING"}`;
            }
            await Promise.allSettled([initSyncPositionsToMemory(), syncRealBrokerBalances(), syncBrokerHoldingsAndOrders()]);
        } else {
            if (statusBox) {
                statusBox.className = "p-2 rounded-xl text-[10px] mono-font font-bold bg-[#eb5b3c]/15 border border-[#eb5b3c]/40 text-[#eb5b3c] block";
                statusBox.textContent = res.data?.message || res.data?.error || "Fast Exit failed — Check Broker OrderBook!";
            }
        }
    } finally {
        isOrderDispatching = false;
    }
}

async function initSyncPositionsToMemory() {
    if (!auth.currentUser) return;
    SESSION_VAULT.fastPositionsMap = {};
    const res = await secureApiCall("broker_positions", { method: "GET" });
    if (res.ok && res.data?.success && Array.isArray(res.data.positions)) {
        res.data.positions.forEach(p => {
            SESSION_VAULT.fastPositionsMap[p.id] = p;
            if (p.token) SESSION_VAULT.fastPositionsMap[String(p.token).toUpperCase()] = p;
            if (p.tsym) {
                const upperSym = p.tsym.toUpperCase();
                SESSION_VAULT.fastPositionsMap[upperSym] = p;
                const matchedKey = matchCanonicalOrderKeyFromTsym(upperSym);
                if (matchedKey) SESSION_VAULT.fastPositionsMap[matchedKey] = p;
            }
        });
    }
    renderOpenPositions();
    await syncAutomationPaperPositions();
}

function evaluateMillisecondMtmOnTick(tokenOrSymbol, liveLtp) {
    if (!tokenOrSymbol || !Number.isFinite(liveLtp) || liveLtp <= 0) return;
    const lookupKey = tokenOrSymbol.toString().toUpperCase();

    checkActiveBracketWatchdog(lookupKey, liveLtp);

    const pos = SESSION_VAULT.fastPositionsMap[lookupKey];
    if (pos && pos.avgPrice > 0) {
        pos.liveMtm = pos.qty > 0 ? (liveLtp - pos.avgPrice) * pos.qty : (pos.avgPrice - liveLtp) * Math.abs(pos.qty);
    }

    let totalPnlInr = 0;
    let fxMissingForCrypto = false;
    const seen = new Set();

    for (const k in SESSION_VAULT.fastPositionsMap) {
        const p = SESSION_VAULT.fastPositionsMap[k];
        if (seen.has(p.id)) continue;
        seen.add(p.id);

        const unrealized = p.liveMtm !== undefined ? p.liveMtm : (p.upnl || 0);
        const legPnl = unrealized + (p.rpnl || 0);

        if (p.quoteCurrency === "USDT" || p.assetClass === "CRYPTO_FUTURES") {
            if (!FX_RATE_STATE.usdToInr || FX_RATE_STATE.quality === "OFFLINE") {
                fxMissingForCrypto = true;
            } else {
                totalPnlInr += legPnl * FX_RATE_STATE.usdToInr;
            }
        } else {
            totalPnlInr += legPnl;
        }
    }

    SESSION_VAULT.dailyLossPnl = totalPnlInr;

    const activeLim = getActiveDailyLossLimitInr();
    if (seen.size > 0 && !fxMissingForCrypto) {
        syncDailyPnlToServer(totalPnlInr, totalPnlInr <= activeLim);
    }
    if (seen.size > 0 && !fxMissingForCrypto && totalPnlInr <= activeLim && !SESSION_VAULT.serverRiskState?.isHardLocked) {
        SESSION_VAULT.serverRiskState.isHardLocked = true;
        applyHardLockUi(totalPnlInr);
    }

    const now = performance.now();
    if (now - lastUiUpdateTime > 250) {
        lastUiUpdateTime = now;
        renderOpenPositions();
        const mtmEl = document.getElementById("hudLiveMtmVal");
        const floatChip = document.getElementById("hudFloatingMtmChip");
        const formatted = `${totalPnlInr >= 0 ? "+" : "-"}₹${Math.abs(totalPnlInr).toFixed(2)}`;

        if (mtmEl) {
            if (fxMissingForCrypto) {
                mtmEl.textContent = "FX UNAVAILABLE";
                mtmEl.className = "text-amber-600 font-black";
            } else {
                mtmEl.textContent = formatted;
                mtmEl.className = totalPnlInr >= 0 ? "text-[#00d09c] font-black" : "text-[#eb5b3c] font-black";
            }
        }

        if (floatChip) {
            if (seen.size > 0 && !fxMissingForCrypto) {
                floatChip.classList.remove("hidden");
                floatChip.textContent = formatted;
                floatChip.className = totalPnlInr >= 0
                    ? "cursor-pointer text-[9.5px] font-mono font-black px-2 py-1 rounded-xl bg-[#00d09c]/15 border border-[#00d09c]/40 text-[#059669] shadow-sm"
                    : "cursor-pointer text-[9.5px] font-mono font-black px-2 py-1 rounded-xl bg-[#eb5b3c]/15 border border-[#eb5b3c]/40 text-[#eb5b3c] shadow-sm";
            } else {
                floatChip.classList.add("hidden");
            }
        }
    }
}

function checkActiveBracketWatchdog(symbol, currentPrice) {
    const bracket = SESSION_VAULT.activeBracketOrders[symbol];
    if (!bracket || !bracket.isActive || bracket.state !== "ACTIVE") return;
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) return;

    const isBuy = bracket.side === "BUY";
    const isSlHit = isBuy ? (currentPrice <= bracket.sl) : (currentPrice >= bracket.sl);
    if (isSlHit) {
        bracket.state = "EXIT_PENDING";
        bracket.isActive = false;
        dispatchLiveBrokerTrade(isBuy ? "SELL" : "BUY", symbol, bracket.qty, {
            operationType: "EXIT", reason: "STOP_LOSS", mode: LIVE_AUTO_TRADING_ENABLED ? "LIVE" : "PAPER"
        });
        return;
    }

    const isTargetHit = isBuy ? (currentPrice >= bracket.target) : (currentPrice <= bracket.target);
    if (isTargetHit) {
        bracket.state = "EXIT_PENDING";
        bracket.isActive = false;
        dispatchLiveBrokerTrade(isBuy ? "SELL" : "BUY", symbol, bracket.qty, {
            operationType: "EXIT", reason: "TARGET", mode: LIVE_AUTO_TRADING_ENABLED ? "LIVE" : "PAPER"
        });
        return;
    }
}

function dispatchManualDeskOrder(side) {
    const symbol = document.getElementById("tradeOrderSymbol")?.value;
    const qty = Number(document.getElementById("tradeOrderQty")?.value);
    const canonical = resolveCanonicalInstrument(symbol);
    if (!canonical) { alert(`Unsupported symbol: ${symbol}`); return; }

    const activePos = SESSION_VAULT.fastPositionsMap[symbol] || SESSION_VAULT.fastPositionsMap[canonical.token];
    if (activePos && Number(activePos.qty) !== 0) {
        const isClosingOpposite = (activePos.qty > 0 && side === "SELL") || (activePos.qty < 0 && side === "BUY");
        if (isClosingOpposite) {
            const closeConfirm = confirm(`You have an OPEN ${activePos.qty > 0 ? "LONG" : "SHORT"} position (${Math.abs(activePos.qty)} qty) in ${symbol}.\n\nClick OK to CLOSE/EXIT this position cleanly (Protected Exit).\nClick Cancel to abort.`);
            if (closeConfirm) {
                const closeQty = Math.min(qty, Math.abs(activePos.qty));
                dispatchLiveBrokerTrade(side, symbol, closeQty, { operationType: "EXIT", mode: LIVE_AUTO_TRADING_ENABLED ? "LIVE" : "PAPER" });
            }
            return;
        }
    }

    const asset = MASTER_ASSETS.find(a => a.orderKey === symbol) || DYNAMIC_SCAN_CACHE.find(a => a.orderKey === symbol);
    const ltp = asset?.priceNum || asset?.price || 0;
    const qual = asset?.dataEnvelope?.quality || asset?.dataQuality || "OFFLINE";

    if (qual === "OFFLINE" || qual === "STALE" || qual === "INVALID" || ltp <= 0) {
        alert(`Blocked: Market feed for ${symbol} is ${qual}. New entries require fresh live/delayed feed.`);
        return;
    }

    const sr = calculateDynamicSupportResistance(asset?.high || ltp, asset?.low || ltp, ltp);
    const s1 = Number(sr.s1);
    const r1 = Number(sr.r1);
    let finalSl = side === "BUY"
        ? (Number.isFinite(s1) && s1 > 0 && s1 < ltp ? s1 : Number((ltp * 0.994).toFixed(2)))
        : (Number.isFinite(r1) && r1 > ltp ? r1 : Number((ltp * 1.006).toFixed(2)));

    const slInp = document.getElementById("tradeSlPriceInput");
    if (slInp) slInp.value = String(finalSl);
    calculateSafeQtySuggestion();

    const capInr = Number(SESSION_VAULT.serverRiskState?.capitalInr) || Number(document.getElementById("riskCapitalInput")?.value) || 100000;
    const requestedRiskPct = Number(document.getElementById("riskPerTradePctInput")?.value || 1);
    const hardRiskPct = Number(SESSION_VAULT.serverRiskState?.maxPerTradeRiskPct || 2);
    const effectiveRiskPct = Math.min(requestedRiskPct, hardRiskPct);
    const maxTradeRiskInr = capInr * effectiveRiskPct / 100;
    let estRiskInr = Math.abs(ltp - finalSl) * qty;
    if (canonical.broker === "COINDCX") {
        if (!FX_RATE_STATE.usdToInr) {
            alert("Blocked: Live USD/INR FX rate unavailable for risk calculation.");
            return;
        }
        estRiskInr = estRiskInr * FX_RATE_STATE.usdToInr;
    }
    if (estRiskInr > maxTradeRiskInr) {
        alert(`Quantity Risk Cap Exceeded!\nEstimated SL Risk: ₹${Math.round(estRiskInr).toLocaleString("en-IN")}\nMax Allowed Per-Trade Risk (${effectiveRiskPct}%): ₹${Math.round(maxTradeRiskInr).toLocaleString("en-IN")}\nPlease reduce Quantity.`);
        return;
    }

    const mode = LIVE_AUTO_TRADING_ENABLED ? "LIVE" : "PAPER";
    const confirmed = confirm(
        `🚨 [${mode} ORDER CONFIRMATION]\n` +
        `Instrument: ${symbol} (${canonical.exchange})\n` +
        `Side: ${side} | Qty: ${qty}\n` +
        `Protective Stop-Loss: ${finalSl}\n` +
        `Broker: ${canonical.broker}\n\n` +
        `This action is a paper simulation; no broker order will be sent.\n\nConfirm?`
    );
    if (!confirmed) return;

    dispatchLiveBrokerTrade(side, symbol, qty, { operationType: "ENTRY", slPrice: finalSl, mode });
}

async function dispatchLiveBrokerTrade(side, overrideSymbol = null, overrideQty = null, orderContext = { operationType: "ENTRY" }) {
    if (isOrderDispatching) return;
    isOrderDispatching = true;
    const statusBox = document.getElementById("orderStatusMsg");
    if (statusBox) statusBox.classList.remove("hidden");

    try {
        const opType = orderContext?.operationType === "EXIT" ? "EXIT" : "ENTRY";
        const mode = "PAPER";

        if (opType === "ENTRY") {
            if (!isTerminalUnlocked || !SESSION_VAULT.pinSessionToken) {
                document.getElementById("quickUnlockModal")?.classList.remove("hidden");
                return;
            }
            if (checkHardKillSwitchLock()) { alert("Server Hard-Lock Active! New entries blocked."); return; }
            if (!checkProAccess()) return;
        }

        const rawSymbol = overrideSymbol || document.getElementById("tradeOrderSymbol")?.value;
        const canonical = resolveCanonicalInstrument(rawSymbol);
        if (!canonical) {
            if (statusBox) statusBox.textContent = `Unmapped symbol: ${rawSymbol}`;
            return;
        }

       const asset = MASTER_ASSETS.find(a => a.orderKey === rawSymbol) || DYNAMIC_SCAN_CACHE.find(a => a.orderKey === rawSymbol);
        const qual = asset?.dataEnvelope?.quality || asset?.dataQuality || "OFFLINE";
        if (opType === "ENTRY" && (qual === "OFFLINE" || qual === "STALE" || qual === "INVALID")) {
            if (statusBox) statusBox.textContent = `Blocked: Feed is ${qual}. Wait for fresh tick.`;
            return;
        }

        let qty = overrideQty !== null ? Number(overrideQty) : Number(document.getElementById("tradeOrderQty")?.value);
        if (!Number.isFinite(qty) || qty <= 0) {
            if (statusBox) statusBox.textContent = "Invalid quantity!";
            return;
        }

        if (opType === "EXIT") {
            const activePos = SESSION_VAULT.fastPositionsMap[rawSymbol] || SESSION_VAULT.fastPositionsMap[canonical.token];
            const remainingQty = activePos ? Math.abs(activePos.qty) : 0;
            if (mode === "LIVE" && remainingQty === 0) {
                if (statusBox) statusBox.textContent = "No open position found in broker PositionBook.";
                return;
            }
            if (remainingQty > 0) qty = Math.min(qty, remainingQty);
        } else if (canonical.lotSize > 1 && canonical.broker === "SHOONYA" && qty % canonical.lotSize !== 0) {
            qty = Math.max(canonical.lotSize, Math.round(qty / canonical.lotSize) * canonical.lotSize);
        }

       const arrivalPrice = asset?.priceNum || asset?.price || 0;
        const clientOrderId = `ORD_${Date.now()}_${crypto.getRandomValues(new Uint32Array(1))[0]}`;

        if (opType === "ENTRY") {
            const response = await fastApiRequest("/automation/opportunities");
            const opportunity = response.ok
                ? (response.data?.opportunities || []).find(item =>
                    item.orderKey === rawSymbol &&
                    item.side === side &&
                    item.executionEligible === true &&
                    Number(item.fusionScore) >= 70
                )
                : null;
            if (!opportunity) {
                if (statusBox) statusBox.textContent = "Blocked: no unexpired server-validated 70+ opportunity for this instrument and direction.";
                return;
            }
            if (!confirm(`[SERVER-VALIDATED PAPER TRADE]\n${opportunity.symbol} ${opportunity.side} • Qty ${opportunity.quantity}\nEntry ${opportunity.entry} • SL ${opportunity.sl} • TP ${opportunity.target} • R:R ${opportunity.rrRatio}\nProceed?`)) return;
            const claimed = await fastApiRequest(`/automation/opportunities/${encodeURIComponent(opportunity.id)}/claim`, {
                method: "POST",
                body: {}
            });
            if (!claimed.ok || !claimed.data?.success) {
                const detail = claimed.data?.detail;
                if (statusBox) statusBox.textContent = typeof detail === "string" ? detail : (detail?.code || "Server opportunity could not be claimed.");
                return;
            }
            const order = claimed.data.order;
            if (order?.risk) applyRiskStateToUi(order.risk);
            recordAndSyncTradeLog(
                rawSymbol,
                side,
                opportunity.quantity,
                canonical.broker,
                order?.state || "PAPER_FILLED",
                order?.client_order_id || opportunity.id
            );
            if (statusBox) statusBox.textContent = `${order?.state || "PAPER_FILLED"} (PAPER) • Server size ${opportunity.quantity} • R:R ${opportunity.rrRatio}`;
            return;
        }

        const res = await fastApiRequest("/orders/place", {
            method: "POST",
            body: {
                symbol: rawSymbol,
                exchange: canonical.exchange,
                side,
                quantity: qty,
                operation: opType,
                mode,
                entry_price: opType === "ENTRY" ? arrivalPrice : null,
                stop_loss: opType === "ENTRY" && orderContext?.slPrice ? Number(orderContext.slPrice) : null,
                take_profit: null,
                client_order_id: clientOrderId
            }
        });

        if (res.ok && res.data?.success) {
            if (res.data.risk) applyRiskStateToUi(res.data.risk);
            const orderState = res.data.state || "ACCEPTED";
            const orderId = res.data.broker_order_id || res.data.client_order_id || clientOrderId;
            if (statusBox) {
                statusBox.className = "p-2 rounded-xl text-[10px] mono-font font-bold bg-[#00d09c]/15 border border-[#00d09c]/40 text-[#059669] block";
                statusBox.textContent = `${orderState} (${res.data.mode}) • ID: ${orderId} • Protection: ${res.data.protection_status || "-"}`;
            }
            recordAndSyncTradeLog(rawSymbol, side, qty, canonical.broker, orderState, orderId);
            if (orderState === "PAPER_FILLED") {
                recordExecutionQualityMetric({ symbol: rawSymbol, arrivalPrice, fillPrice: arrivalPrice, status: "PAPER_FILLED" });
            }

        } else {
            if (res.data?.risk) applyRiskStateToUi(res.data.risk);
            recordExecutionQualityMetric({ symbol: rawSymbol, arrivalPrice, fillPrice: null, status: "REJECTED" });
            if (statusBox) {
                statusBox.className = "p-2 rounded-xl text-[10px] mono-font font-bold bg-[#eb5b3c]/15 border border-[#eb5b3c]/40 text-[#eb5b3c] block";
                const detail = res.data?.detail;
                statusBox.textContent = typeof detail === "string"
                    ? detail
                    : detail?.reason || detail?.message || detail?.code || res.data?.message || "Order Rejected";
            }
        }
    } catch (err) {
        if (statusBox) statusBox.textContent = "Dispatch error. Reconcile orderbook before retrying.";
    } finally {
        isOrderDispatching = false;
    }
}

async function reconcileBrokerTradeStatus(brokerOrderId, broker, symbol, clientOrderId = "", arrivalPrice = 0, attempt = 1) {
    const statusBox = document.getElementById("orderStatusMsg");
    try {
        const res = await secureApiCall("reconcile_order", {
            method: "POST",
            jsonBody: { broker, brokerOrderId, clientOrderId }
        });
        if (res.ok && res.data?.success) {
            const st = res.data.status;
            updateTradeLogStatus(brokerOrderId, st, res.data.avgPrice);
            if (statusBox) {
                statusBox.textContent = `${broker} ${symbol}: ${st}${res.data.avgPrice ? ` @ ${res.data.avgPrice}` : ""}`;
            }
            if (st === "FILLED" || st === "PARTIALLY_FILLED") {
                recordExecutionQualityMetric({ symbol, arrivalPrice, fillPrice: res.data.avgPrice, status: st });
                syncRealBrokerBalances();
                initSyncPositionsToMemory();
            } else if ((st === "ACCEPTED" || st === "OPEN" || st === "UNKNOWN") && attempt < 4) {
                setTimeout(() => reconcileBrokerTradeStatus(brokerOrderId, broker, symbol, clientOrderId, arrivalPrice, attempt + 1), attempt * 2500);
            } else if (st === "REJECTED" || st === "CANCELLED") {
                recordExecutionQualityMetric({ symbol, arrivalPrice, fillPrice: null, status: st });
            }
        }
    } catch (e) {
        console.warn("[Reconcile Error]:", e.message);
    }
}

function updateTradeLogStatus(orderId, finalStatus, fillPrice = null) {
    const item = SESSION_VAULT.tradeHistory.find(t => t.orderId === orderId);
    if (item) {
        item.status = finalStatus;
        if (fillPrice) item.fillPrice = fillPrice;
        renderTradeHistory();
    }
}

function renderTradeHistory() {
    const container = document.getElementById("recentTradesList");
    if (!container) return;
    if (!SESSION_VAULT.tradeHistory || SESSION_VAULT.tradeHistory.length === 0) {
        container.innerHTML = `<p class="text-[9px] text-slate-500 font-mono text-center py-2">No executions recorded in this session.</p>`;
        return;
    }
    container.innerHTML = "";
    SESSION_VAULT.tradeHistory.slice(0, 8).forEach(t => {
        const row = document.createElement("div");
        row.className = "flex items-center justify-between p-2 rounded-xl bg-slate-50 border border-slate-200 text-[10px] mono-font";
        const left = document.createElement("div");
        const top = document.createElement("div");
        top.className = "flex items-center space-x-1.5";
        const sTag = document.createElement("span");
        sTag.className = `px-1.5 py-0.2 rounded text-[8px] font-black ${t.side === "BUY" ? "bg-[#00d09c]/20 text-[#059669]" : "bg-[#eb5b3c]/20 text-[#eb5b3c]"}`;
        sTag.textContent = t.side;
        const bSym = document.createElement("b");
        bSym.className = "text-slate-900";
        bSym.textContent = t.symbol;
        top.append(sTag, bSym);
        const sub = document.createElement("span");
        sub.className = "text-[8.5px] text-slate-500 font-sans";
        sub.textContent = `${t.time} • ${t.broker}`;
        left.append(top, sub);

        const right = document.createElement("div");
        right.className = "text-right";
        const bQty = document.createElement("b");
        bQty.className = "text-slate-700 block";
        bQty.textContent = `${t.qty} Qty`;
        const stSpan = document.createElement("span");
        stSpan.className = `text-[8px] font-bold ${(t.status === "FILLED" || t.status === "PAPER_FILLED") ? "text-[#059669]" : (t.status === "ACCEPTED" ? "text-blue-600" : "text-[#eb5b3c]")}`;
        stSpan.textContent = t.status;
        right.append(bQty, stSpan);

        row.append(left, right);
        container.appendChild(row);
    });
}

function recordAndSyncTradeLog(symbol, side, qty, broker, status = "ACCEPTED", orderId = "") {
    SESSION_VAULT.tradeHistory.unshift({
        id: "TXN_" + Date.now().toString().slice(-6),
        orderId, symbol, side, qty, broker, status,
        time: new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Kolkata" })
    });
    renderTradeHistory();
}


function updateBrokerConnectionDots(shoonyaLive, shoonyaLinked, coindcxLive, coindcxLinked) {
    const sLiveDot = document.getElementById("shoonyaLiveDot");
    const sLiveText = document.getElementById("shoonyaLiveText");
    const cLiveDot = document.getElementById("coindcxLiveDot");
    const cLiveText = document.getElementById("coindcxLiveText");
    if (sLiveDot) sLiveDot.className = shoonyaLive ? "status-dot-green-1s shrink-0" : "status-dot-red-1s shrink-0";
    if (sLiveText) {
        sLiveText.textContent = "LIVE";
        sLiveText.className = shoonyaLive ? "text-[8px] text-[#00d09c] font-mono" : "text-[8px] text-[#eb5b3c] font-mono";
    }
    if (cLiveDot) cLiveDot.className = coindcxLive ? "status-dot-green-1s shrink-0" : "status-dot-red-1s shrink-0";
    if (cLiveText) {
        cLiveText.textContent = "LIVE";
        cLiveText.className = coindcxLive ? "text-[8px] text-[#00d09c] font-mono" : "text-[8px] text-[#eb5b3c] font-mono";
    }

    const sDot = document.getElementById("shoonyaStatusDot");
    const sTxt = document.getElementById("shoonyaStatusText");
    const sBanUnlink = document.getElementById("bannerShoonyaUnlinkBtn");
    const cDot = document.getElementById("coindcxStatusDot");
    const cTxt = document.getElementById("coindcxStatusText");
    const cBanUnlink = document.getElementById("bannerCoindcxUnlinkBtn");

    if (sDot && sTxt) {
        sDot.className = shoonyaLive ? "status-dot-green-1s shrink-0" : "status-dot-red-1s shrink-0";
        if (shoonyaLive) {
            sTxt.textContent = "LIVE";
            sTxt.className = "text-[#00d09c] font-black";
            sTxt.onclick = null;
        } else if (shoonyaLinked) {
            sTxt.textContent = "Connecting...";
            sTxt.className = "cursor-pointer text-[#eb5b3c] font-black";
            sTxt.onclick = () => switchTab("Settings");
        } else {
            sTxt.textContent = "NOT_CONNECTED";
            sTxt.className = "cursor-pointer text-[#eb5b3c] underline decoration-dotted font-bold";
            sTxt.onclick = () => switchTab("Settings");
        }
    }
    if (sBanUnlink) sBanUnlink.classList.toggle("hidden", !shoonyaLinked);

    if (cDot && cTxt) {
        cDot.className = coindcxLive ? "status-dot-green-1s shrink-0" : "status-dot-red-1s shrink-0";
        if (coindcxLive) {
            cTxt.textContent = "LIVE";
            cTxt.className = "text-[#00d09c] font-black";
            cTxt.onclick = null;
        } else if (coindcxLinked) {
            cTxt.textContent = "Connecting...";
            cTxt.className = "cursor-pointer text-[#eb5b3c] font-black";
            cTxt.onclick = () => switchTab("Settings");
        } else {
            cTxt.textContent = "NOT_CONNECTED";
            cTxt.className = "cursor-pointer text-[#eb5b3c] underline decoration-dotted font-bold";
            cTxt.onclick = () => switchTab("Settings");
        }
    }
    if (cBanUnlink) cBanUnlink.classList.toggle("hidden", !coindcxLinked);
}

async function autoConnectShoonyaBroker() {
    const uidEl = document.getElementById("settingShoonyaUid");
    const pwdEl = document.getElementById("settingShoonyaPassword");
    const factor2El = document.getElementById("settingShoonyaTotp");
    const vendorCodeEl = document.getElementById("settingShoonyaVendor");
    const apiKeyEl = document.getElementById("settingShoonyaApiKey");
    const btn = document.getElementById("btnShoonyaAutoConnect");
    const uid = uidEl?.value.trim().toUpperCase() || "";
    const password = pwdEl?.value || "";
    const factor2 = factor2El?.value.trim() || "";
    const vendorCode = vendorCodeEl?.value.trim().toUpperCase() || "";
    const apiKey = apiKeyEl?.value.trim() || "";
    if (!uid || !password || !factor2) {
        alert("Please enter your Shoonya User ID, Password, and 6-Digit TOTP / PAN!");
        return;
    }

    if (btn) { btn.disabled = true; btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin mr-1"></i> Generating Shoonya Session...`; }

    try {
        const res = await secureApiCall("shoonya_auto_login", {
            method: "POST"
        });

        if (res.ok && res.data?.success) {
            SESSION_VAULT.shoonyaLinked = true;
            ["settingShoonyaPassword", "settingShoonyaTotp", "settingShoonyaApiKey"].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
            updateVaultStatusBadges();
            await Promise.allSettled([
                syncRealBrokerBalances(),
                initSyncPositionsToMemory(),
                syncBrokerHoldingsAndOrders(),
                fetchMasterOrPublicIndianFeed()
            ]);
            alert(`✅ Shoonya Connected Successfully! Live Feed Active for ${res.data.uname}`);
        } else {
            alert(`❌ Shoonya Login Failed: ${res.data?.error || "Shoonya vault not configured or credentials invalid"}`);
        }
} finally {
        if (btn) { btn.disabled = false; btn.innerHTML = `<i class="fa-solid fa-bolt mr-1"></i> Auto-Generate Token & Connect Shoonya`; }
    }
}

async function refreshBrokerVaultStatus() {
    if (!auth.currentUser) return;
    const res = await secureApiCall("get_broker_vault_status", { method: "GET" });
    if (res.ok && res.data?.success) {
        SESSION_VAULT.shoonyaLinked = !!res.data.shoonyaLinked;
        SESSION_VAULT.coindcxLinked = !!res.data.coindcxLinked;
        updateVaultStatusBadges();
        updateBrokerConnectionDots(false, SESSION_VAULT.shoonyaLinked, false, SESSION_VAULT.coindcxLinked);
    } else {
        document.getElementById("btnShoonyaUnlink")?.classList.remove("hidden");
        document.getElementById("btnCoindcxUnlink")?.classList.remove("hidden");
    }
}

function updateVaultStatusBadges() {
    const sB = document.getElementById("shoonyaVaultStatusBadge");
    const cB = document.getElementById("coindcxVaultStatusBadge");
    const sUnlink = document.getElementById("btnShoonyaUnlink");
    const cUnlink = document.getElementById("btnCoindcxUnlink");

    if (sB) {
        sB.textContent = SESSION_VAULT.shoonyaLinked ? "LINKED (AES-GCM)" : "NOT_CONNECTED";
        sB.className = SESSION_VAULT.shoonyaLinked
            ? "text-[8px] bg-[#00d09c]/20 text-[#059669] px-1.5 py-0.2 rounded font-mono font-bold border border-[#00d09c]/30"
            : "text-[8px] bg-[#eb5b3c]/10 text-[#eb5b3c] px-1.5 py-0.2 rounded font-mono font-bold border border-[#eb5b3c]/30";
    }
    if (sUnlink) sUnlink.classList.toggle("hidden", !SESSION_VAULT.shoonyaLinked);

    if (cB) {
        cB.textContent = SESSION_VAULT.coindcxLinked ? "LINKED (AES-GCM)" : "NOT_CONNECTED";
        cB.className = SESSION_VAULT.coindcxLinked
            ? "text-[8px] bg-amber-500/20 text-amber-600 px-1.5 py-0.2 rounded font-mono font-bold border border-amber-500/30"
            : "text-[8px] bg-[#eb5b3c]/10 text-[#eb5b3c] px-1.5 py-0.2 rounded font-mono font-bold border border-[#eb5b3c]/30";
    }
    if (cUnlink) cUnlink.classList.toggle("hidden", !SESSION_VAULT.coindcxLinked);

    applySettingsCredentialLock("shoonya", SESSION_VAULT.shoonyaLinked);
    applySettingsCredentialLock("coindcx", SESSION_VAULT.coindcxLinked);

    handleOrderSymbolChange();
}

async function unlinkBrokerVault(brokerName) {
    const ok = confirm(`Are you sure you want to Force-Unlink ${brokerName} and wipe all saved keys, tokens & balances?`);
    if (!ok) return;

    const res = await secureApiCall("delete_broker_vault", {
        method: "POST",
        jsonBody: { broker: brokerName }
    });
    if (res.ok && res.data?.success) {
        if (brokerName === "SHOONYA") {
            SESSION_VAULT.shoonyaLinked = false;
            SESSION_VAULT.shoonyaLiveBalance = "₹0.00";
            SESSION_VAULT.shoonyaHoldingsList = [];
            ["settingShoonyaUid", "settingShoonyaVendor", "settingShoonyaPassword", "settingShoonyaTotp", "settingShoonyaApiKey", "settingShoonyaToken"].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
            if (activePositionBroker === "shoonya") {
                const bal = document.getElementById("hudShoonyaBalance");
                if (bal) bal.textContent = "₹0.00";
            }
        }
        if (brokerName === "COINDCX") {
            SESSION_VAULT.coindcxLinked = false;
            SESSION_VAULT.coindcxUsdtNum = 0;
            SESSION_VAULT.coindcxLiveBalance = "0.00 USDT";
            SESSION_VAULT.coindcxHoldingsList = [];
            ["settingCoinDcxKey", "settingCoinDcxSecret"].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
            const elInr = document.getElementById("hudCoinDcxInrVal");
            const elUsdt = document.getElementById("hudCoinDcxUsdtVal");
            if (elInr) elInr.textContent = "₹0.00";
            if (elUsdt) elUsdt.textContent = "0.00 USDT";
            if (activePositionBroker === "coindcx") {
                const bal = document.getElementById("hudShoonyaBalance");
                if (bal) bal.textContent = "0.00 USDT";
            }
        }
        updateVaultStatusBadges();
        updateBrokerConnectionDots(false, SESSION_VAULT.shoonyaLinked, false, SESSION_VAULT.coindcxLinked);
        initSyncPositionsToMemory();
        renderHoldingsItems();
        fetchMasterOrPublicIndianFeed();
        syncCoinDcxOrBinanceFeed();
        alert(`✅ ${brokerName} Force-Unlinked cleanly!`);
    } else {
        alert(res.data?.error || "Failed to unlink broker vault.");
    }
}

async function saveShoonyaVaultToRamAndCloud() {
    const uidEl = document.getElementById("settingShoonyaUid");
    const tokEl = document.getElementById("settingShoonyaToken");
    const shoonyaUid = uidEl?.value.trim().toUpperCase() || "";
    const shoonyaToken = tokEl?.value.trim() || "";
    if (!shoonyaUid || !shoonyaToken) { alert("Enter both Shoonya User ID & susertoken."); return; }

    const res = await secureApiCall("save_broker_vault", {
        method: "POST",
        jsonBody: { broker: "SHOONYA", payload: { shoonyaUid, shoonyaToken } }
    });
    if (res.ok && res.data?.success) {
        SESSION_VAULT.shoonyaLinked = true;
        if (tokEl) tokEl.value = "";
        updateVaultStatusBadges();
        await Promise.allSettled([
            syncRealBrokerBalances(),
            initSyncPositionsToMemory(),
            syncBrokerHoldingsAndOrders(),
            fetchMasterOrPublicIndianFeed()
        ]);
        alert("Shoonya token encrypted & saved! Live feed activated.");
    } else {
        alert(`Vault Error: ${res.data?.error || "Failed to save"}`);
    }
}

async function saveCoinDcxVaultToRamAndCloud() {
    const keyEl = document.getElementById("settingCoinDcxKey");
    const secEl = document.getElementById("settingCoinDcxSecret");
    const coindcxKey = keyEl?.value.trim() || "";
    const coindcxSecret = secEl?.value.trim() || "";
    if (!coindcxKey || !coindcxSecret) { alert("Enter both CoinDCX API Key & Secret."); return; }

    const res = await secureApiCall("save_broker_vault", {
        method: "POST",
        jsonBody: { broker: "COINDCX", payload: { coindcxKey, coindcxSecret } }
    });
    if (res.ok && res.data?.success) {
        SESSION_VAULT.coindcxLinked = true;
        if (keyEl) keyEl.value = "";
        if (secEl) secEl.value = "";
        updateVaultStatusBadges();
        await Promise.allSettled([
            syncRealBrokerBalances(),
            initSyncPositionsToMemory(),
            syncBrokerHoldingsAndOrders(),
            syncCoinDcxOrBinanceFeed()
        ]);
        alert("CoinDCX credentials encrypted & saved in Server Vault!");
    } else {
        alert(`Vault Error: ${res.data?.error || "Failed to save"}`);
    }
}

async function syncRealBrokerBalances() {
    if (!auth.currentUser) return;
    
    // Fetch Shoonya balance
    let shoonyaBalance = { linked: false, status: "NOT_CONNECTED", cash: 0, margin: 0, available_margin: 0 };
    try {
        const sRes = await secureApiCall("shoonya_balance", { method: "GET" });
        if (sRes.ok && sRes.data?.success && sRes.data?.balance) {
            shoonyaBalance = sRes.data.balance;
        }
    } catch (e) {
        console.warn("[Shoonya Balance]:", e.message);
    }
    
    // Fetch CoinDCX balance (dual wallets)
    let coindcxBalance = { linked: false, status: "NOT_CONNECTED", spot_wallet_usdt: 0, futures_wallet_usdt: 0, inr_deposit: 0 };
    try {
        const cRes = await secureApiCall("coindcx_balance", { method: "GET" });
        if (cRes.ok && cRes.data?.success && cRes.data?.balance) {
            coindcxBalance = cRes.data.balance;
        }
    } catch (e) {
        console.warn("[CoinDCX Balance]:", e.message);
    }

    const sLive = !!(shoonyaBalance.linked && shoonyaBalance.status === "LIVE");
    const cLive = !!(coindcxBalance.linked && coindcxBalance.status === "LIVE");

    SESSION_VAULT.shoonyaLinked = !!shoonyaBalance.linked;
    SESSION_VAULT.coindcxLinked = !!coindcxBalance.linked;
    updateBrokerConnectionDots(sLive, SESSION_VAULT.shoonyaLinked, cLive, SESSION_VAULT.coindcxLinked);

    // Update Shoonya balance display
    const shoonyaCash = Number(shoonyaBalance.cash || 0);
    const shoonyaAvailable = Number(shoonyaBalance.available_margin || shoonyaBalance.margin || 0);
    const shoonyaUsed = Number(shoonyaBalance.used_margin || 0);
    
    SESSION_VAULT.shoonyaLiveBalance = `₹${shoonyaCash.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
    
    const availCashEl = document.getElementById("availCash");
    const marginUsedEl = document.getElementById("marginUsed");
    const portValEl = document.getElementById("portVal");
    
    if (availCashEl) availCashEl.textContent = `₹${shoonyaAvailable.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
    if (marginUsedEl) marginUsedEl.textContent = `₹${shoonyaUsed.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
    if (portValEl) portValEl.textContent = `₹${(shoonyaCash + shoonyaAvailable).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

    // Update CoinDCX dual wallet display
    const spotUsdt = Number(coindcxBalance.spot_wallet_usdt || 0);
    const futuresUsdt = Number(coindcxBalance.futures_wallet_usdt || 0);
    const inrDeposit = Number(coindcxBalance.inr_deposit || 0);
    
    SESSION_VAULT.coindcxUsdtNum = spotUsdt + futuresUsdt;
    SESSION_VAULT.coindcxLiveBalance = `${(spotUsdt + futuresUsdt).toFixed(2)} USDT`;
    
    const elInr = document.getElementById("hudCoinDcxInrVal");
    const elSpotUsdt = document.getElementById("hudCoinDcxSpotUsdtVal");
    const elFuturesUsdt = document.getElementById("hudCoinDcxFuturesUsdtVal");
    const elDcxBadge = document.getElementById("coindcxWalletStatusBadge");
    
    if (elInr) elInr.textContent = `₹${inrDeposit.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
    if (elSpotUsdt) elSpotUsdt.textContent = `${spotUsdt.toFixed(2)} USDT`;
    if (elFuturesUsdt) elFuturesUsdt.textContent = `${futuresUsdt.toFixed(2)} USDT`;
    
    if (elDcxBadge) {
        if (cLive) {
            elDcxBadge.textContent = "LIVE • SPOT + FUTURES";
            elDcxBadge.className = "text-[8.5px] bg-[#00d09c]/15 text-[#059669] font-mono font-bold px-2 py-0.5 rounded border border-[#00d09c]/30";
        } else {
            elDcxBadge.textContent = coindcxBalance.linked ? "CONNECTED" : "NOT_CONNECTED";
            elDcxBadge.className = "text-[8.5px] bg-[#eb5b3c]/15 text-[#eb5b3c] font-mono font-bold px-2 py-0.5 rounded border border-[#eb5b3c]/30";
        }
    }
}

async function syncBrokerHoldingsAndOrders() {
    if (!auth.currentUser) return;
    const [hRes, oRes] = await Promise.allSettled([
        secureApiCall("broker_holdings", { method: "GET" }),
        secureApiCall("broker_orders", { method: "GET" })
    ]);

    if (hRes.status === "fulfilled" && hRes.value.ok && hRes.value.data?.success) {
        SESSION_VAULT.shoonyaHoldingsList = hRes.value.data.shoonya || [];
        SESSION_VAULT.coindcxHoldingsList = hRes.value.data.coindcx || [];
        renderHoldingsItems();
    }

    if (oRes.status === "fulfilled" && oRes.value.ok && oRes.value.data?.success) {
        SESSION_VAULT.pendingOrdersList = oRes.value.data.pending || [];
        renderPendingOrdersList();
    }
}

function renderPendingOrdersList() {
    const container = document.getElementById("pendingOrdersList");
    if (!container) return;
    const list = (SESSION_VAULT.pendingOrdersList || []).filter(o =>
        activePositionBroker === "shoonya" ? o.broker === "SHOONYA" : o.broker === "COINDCX"
    );
    if (list.length === 0) {
        container.innerHTML = `<p class="text-[9px] text-slate-500 font-mono text-center py-2">No pending ${activePositionBroker.toUpperCase()} orders.</p>`;
        return;
    }
    container.innerHTML = list.map(o => `
        <div class="flex items-center justify-between p-2 rounded-xl bg-slate-50 border border-slate-200 text-[10px] mono-font">
            <div>
                <span class="px-1.5 py-0.2 rounded text-[8px] font-black ${o.side === "BUY" ? "bg-[#00d09c]/20 text-[#059669]" : "bg-[#eb5b3c]/20 text-[#eb5b3c]"}">${sanitizeCleanText(o.side)}</span>
                <b class="text-slate-900 ml-1">${sanitizeCleanText(o.symbol)}</b>
                <span class="text-[8.5px] text-slate-500 block mt-0.5">${o.qty} Qty @ ${o.price}</span>
            </div>
            <span class="text-[8px] font-bold text-amber-600">${sanitizeCleanText(o.status)}</span>
        </div>
    `).join("");
}

function initMobileSpeechUnlocker() {
    // Voice alerts are disabled across the UI.
}

function syncVoiceToggleButtonsUi() {
    const on = SESSION_VAULT.voiceEnabled;
    const btn = document.getElementById("btnVoiceSettingToggle");
    if (btn) {
        btn.textContent = on ? "VOICE ALERTS: ON" : "VOICE ALERTS: OFF";
        btn.className = on
            ? "px-3 py-1.5 rounded-xl bg-[#00d09c] text-white font-black text-[10px] transition active:scale-95 shadow-md"
            : "px-3 py-1.5 rounded-xl bg-[#eb5b3c] text-white font-black text-[10px] transition active:scale-95 shadow-md";
    }
    const hdrBtn = document.getElementById("hdrVoiceToggleBtn");
    const hdrIcon = document.getElementById("hdrVoiceIcon");
    const hdrTxt = document.getElementById("hdrVoiceText");
    if (hdrBtn && hdrIcon && hdrTxt) {
        hdrBtn.className = on
            ? "px-2 py-1 bg-[#00d09c]/10 text-[#059669] text-[9px] font-mono font-bold rounded-xl border border-[#00d09c]/30 active:scale-95 transition flex items-center space-x-1"
            : "px-2 py-1 bg-[#eb5b3c]/10 text-[#eb5b3c] text-[9px] font-mono font-bold rounded-xl border border-[#eb5b3c]/30 active:scale-95 transition flex items-center space-x-1";
        hdrIcon.className = on ? "fa-solid fa-volume-high text-[10px]" : "fa-solid fa-volume-xmark text-[10px]";
        hdrTxt.textContent = on ? "ON" : "OFF";
    }
}

function toggleVoiceAlertState() {
    SESSION_VAULT.voiceEnabled = false;
    stopAiVoice();
    syncVoiceToggleButtonsUi();
}

function changeVoiceLanguage(lang) {
    SESSION_VAULT.voiceLang = "en-US";
    SESSION_VAULT.newsLang = lang || "en-US";
    stopAiVoice();
    ArafCloudMaster.syncAllUserData(null, { preferences: { voiceLang: lang, newsLang: lang, aiChatLang: SESSION_VAULT.aiChatLang } });
}

function changeVoiceInterval(val) {
    SESSION_VAULT.voiceIntervalMinutes = parseInt(val, 10);
    startPeriodicVoiceScanner();
}

function startPeriodicVoiceScanner() {
    if (periodicVoiceIntervalId) clearInterval(periodicVoiceIntervalId);
    if (!SESSION_VAULT.voiceEnabled || SESSION_VAULT.voiceIntervalMinutes === 0) return;
    periodicVoiceIntervalId = setInterval(() => {
        if (!document.hidden && isTerminalUnlocked) {
            runFullMarketDiamondScan(false);
        }
    }, Math.max(1, SESSION_VAULT.voiceIntervalMinutes) * 60 * 1000);
}

function explainTextFriendly(rawText, title = "Market Update") {
    // Voice alerts are removed; preserve call compatibility for existing callers.
}

function pauseResumeAiVoice() {
    return;
}
function stopAiVoice() {
    SESSION_VAULT.voiceEnabled = false;
    isVoicePaused = false;
}

function setAiChatLanguage(langCode) {
    SESSION_VAULT.aiChatLang = langCode;
    ArafCloudMaster.syncAllUserData(null, { preferences: { voiceLang: SESSION_VAULT.voiceLang, newsLang: SESSION_VAULT.newsLang, aiChatLang: langCode } });
}

function isStrictMarketTopic(q) {
    const low = String(q || "").toLowerCase();
    const blocked = ["recipe", "biryani", "movie", "song", "lyrics", "girlfriend", "boyfriend", "love letter", "joke", "poem", "cricket", "football", "astrology", "horoscope", "homework", "python code", "javascript code"];
    if (blocked.some(b => low.includes(b))) return false;
    const marketWords = ["stock", "share", "market", "nifty", "banknifty", "sensex", "bankex", "fno", "option", "call", "put", "future", "crypto", "btc", "eth", "usdt", "coin", "binance", "coindcx", "shoonya", "chart", "candle", "ema", "rsi", "vwap", "atr", "sl", "stop loss", "target", "ipo", "gmp", "sebi", "rbi", "fed", "trap", "breakout", "sweep", "liquidity", "volume", "sector", "buy", "sell", "short", "long", "capital", "risk", "entry", "profit", "loss", "trade", "footprint", "regime", "setup", "support", "resistance", "শেয়ার", "মার্কেট", "নিফটি", "ক্রিপ্টো", "ট্রেড", "স্টপ", "টার্গেট", "চার্ট", "পুঁজি"];
    return marketWords.some(w => low.includes(w));
}

function getInstantQuantAiResponse(query, quantSnap) {
    const asset = currentSelectedAsset || MASTER_ASSETS[0];
    const sr = calculateDynamicSupportResistance(asset.high, asset.low, asset.priceNum);
    const qual = asset.dataEnvelope?.quality || "OFFLINE";
    const tw = quantSnap?.twoWay;
    const relatedNews = allFetchedNewsItems
        .filter(n => isNewsRelatedToAsset(n.title, asset) || isGeopoliticalOrMacro(n.title))
        .slice(0, 3)
        .map(n => n.translatedTitle || n.title);
    const newsLine = relatedNews.length ? `Latest catalysts: ${relatedNews.join(" | ")}` : "Latest catalyst wire: No recent asset-specific alert.";
    return `[Quant Desk Summary]\n📊 ${asset.name} (${qual})\nLTP: ${asset.price} | Setup: ${tw?.direction || "WAIT"} (R:R ${tw?.rrRatio || "--"})\nEMA(9/21): ${quantSnap?.ema9 ?? "--"}/${quantSnap?.ema21 ?? "--"} | RSI: ${quantSnap?.rsi14 ?? "--"}\nS1: ${sr.s1} | R1: ${sr.r1}\n${newsLine}\nRule: Respect Capital Risk Limit with strict Stop-Loss.`;
}

async function sendAiMessage() {
    const input = document.getElementById("aiInput");
    if (!input) return;
    const q = input.value.trim();
    if (!q) return;

    addChatMessage("user", q);
    input.value = "";

    if (!isStrictMarketTopic(q)) {
        const denyMsg = "Strict Market Guard: I only answer questions related to Share Markets (NSE/BSE/F&O/IPO) and Cryptocurrency Trading.";
        addChatMessage("ai", denyMsg);
        return;
    }

    const typingId = addTypingIndicator();
    const asset = currentSelectedAsset || MASTER_ASSETS[0];
    const sr = calculateDynamicSupportResistance(asset.high, asset.low, asset.priceNum);
    const quantSnap = await runQuantOrchestrator(asset.id);
    const langName = SUPPORTED_LANGUAGES[SESSION_VAULT.aiChatLang]?.name || "English";
    const latestCatalysts = allFetchedNewsItems
        .filter(n => isNewsRelatedToAsset(n.title, asset) || isGeopoliticalOrMacro(n.title))
        .slice(0, 5)
        .map(n => n.translatedTitle || n.title)
        .join(" | ");

    const res = await secureApiCall("ai_guardian", {
        method: "POST",
        timeoutMs: 16000,
        jsonBody: {
            query: q,
            langName,
            context: {
                asset: asset.name,
                ltp: asset.price,
                feedQuality: asset.dataEnvelope?.quality || "OFFLINE",
                regime: quantSnap?.regime?.regime || "UNKNOWN",
                twoWaySignal: quantSnap?.twoWay?.direction || "NO TRADE",
                ema9: quantSnap?.ema9 ?? "UNAVAILABLE",
                ema21: quantSnap?.ema21 ?? "UNAVAILABLE",
                rsi14: quantSnap?.rsi14 ?? "UNAVAILABLE",
                atr: quantSnap?.volatility?.atr_value ?? "UNAVAILABLE",
                realizedVol: quantSnap?.volatility?.realized_volatility ?? "UNAVAILABLE",
                vwap: quantSnap?.volIntel?.vwap ?? "UNAVAILABLE",
                corrBtc: quantSnap?.corr?.pearson ?? "UNAVAILABLE",
                s1: sr.s1,
                r1: sr.r1,
                vix: liveIndiaVix ? liveIndiaVix.toFixed(2) : "UNAVAILABLE",
                latestCatalysts: latestCatalysts || "No recent asset-specific catalyst"
            },
            history: AI_CHAT_HISTORY.slice(-4)
        }
    });

    removeTypingIndicator(typingId);
    if (res.ok && res.data?.success && res.data.reply) {
        addChatMessage("ai", res.data.reply);
    } else {
        addChatMessage("ai", res.data?.reply || "AI Guardian temporarily unavailable. Please retry.");
    }
}

function askAiQuick(query) { const input = document.getElementById("aiInput"); if (input) { input.value = query; sendAiMessage(); } }
function addTypingIndicator() {
    const box = document.getElementById("aiChatBox");
    if (!box) return null;
    const id = "typing_" + Date.now();
    const div = document.createElement("div");
    div.id = id;
    div.className = "bg-slate-100 border border-slate-200 text-slate-600 p-2.5 rounded-2xl rounded-tl-none max-w-[85%] text-[10px] flex items-center space-x-2";
    div.innerHTML = `<span class="w-2 h-2 rounded-full bg-[#00d09c] animate-ping"></span><span>Guardian analyzing context...</span>`;
    box.appendChild(div); box.scrollTop = box.scrollHeight;
    return id;
}
function removeTypingIndicator(id) { if (id) document.getElementById(id)?.remove(); }

function addChatMessage(sender, text) {
    const box = document.getElementById("aiChatBox");
    if (!box) return;
    if (sender === "user" || sender === "ai") {
        AI_CHAT_HISTORY.push({ role: sender === "ai" ? "assistant" : "user", content: String(text || "").slice(0, 900) });
        if (AI_CHAT_HISTORY.length > 20) AI_CHAT_HISTORY.shift();
    }
    const div = document.createElement("div");
    if (sender === "user") {
        div.className = "bg-[#00d09c] text-white font-bold p-3 rounded-2xl rounded-tr-none ml-auto max-w-[85%] shadow-md";
        const p = document.createElement("p"); p.className = "font-extrabold text-white text-[9px] mb-1"; p.textContent = "You";
        const c = document.createElement("div"); c.className = "text-[11px] leading-relaxed whitespace-pre-wrap font-sans"; c.textContent = text;
        div.append(p, c);
    } else {
        div.className = "bg-slate-100 border border-slate-200 text-slate-800 p-3 rounded-2xl rounded-tl-none max-w-[90%] shadow-md space-y-1";
        const p = document.createElement("p"); p.className = "font-bold text-[#eb5b3c] text-[10px] mb-1"; p.textContent = "ARAF GUARDIAN";
        const c = document.createElement("div"); c.className = "text-[11px] text-slate-700 leading-relaxed font-sans whitespace-pre-wrap"; c.textContent = text;
        div.append(p, c);
    }
    box.appendChild(div); box.scrollTop = box.scrollHeight;
}

async function refreshSystemHealthAndGovernance() {
    const liveBrokerEl = document.getElementById("govLiveBrokerState");
    const execEl = document.getElementById("govExecQuality");
    const driftEl = document.getElementById("govDriftStatus");
    const feedEl = document.getElementById("govFeedAnomaly");
    const badgeEl = document.getElementById("govHealthBadge");

    let brokerState = (SESSION_VAULT.shoonyaLinked || SESSION_VAULT.coindcxLinked) ? "SESSION_LINKED" : "NOT_CONNECTED";
    let liveArmed = LIVE_AUTO_TRADING_ENABLED;
    if (auth.currentUser) {
        const hRes = await secureApiCall("health", { method: "GET" });
        if (hRes.ok && hRes.data?.ok) {
            brokerState = hRes.data.brokerStatus || brokerState;
            liveArmed = hRes.data.liveTradingEnabled === true;
        }
    }

    if (liveBrokerEl) liveBrokerEl.textContent = `LIVE:${liveArmed} • BRK:${brokerState}`;

    const sc = QUANT_FEATURE_STORE.adaptiveScorecard || { totalRecorded: 0, rejectRatePct: 0 };
    const slipSamples = (QUANT_FEATURE_STORE.executionLog || []).filter(x => Number.isFinite(x.shortfallBps));
    const avgSlip = slipSamples.length > 0 ? (slipSamples.reduce((s, x) => s + x.shortfallBps, 0) / slipSamples.length).toFixed(2) : "0.00";
    if (execEl) execEl.textContent = `${sc.totalRecorded} Ord • ${sc.rejectRatePct}% Rej • ${avgSlip}bps`;

    const staleCount = MASTER_ASSETS.filter(a => a.dataEnvelope?.quality === "STALE" || a.dataEnvelope?.quality === "OFFLINE").length;
    const anomCount = (QUANT_FEATURE_STORE.anomalyLog || []).length;
    if (feedEl) feedEl.textContent = `Stale:${staleCount}/${MASTER_ASSETS.length} • Anom:${anomCount}`;

    const wfCheck = currentSelectedAsset ? runWalkForwardValidation(currentSelectedAsset.id) : null;
    const driftFlag = (sc.rejectRatePct > 25 || (wfCheck?.brierScore !== null && wfCheck?.brierScore > 0.30)) ? "DRIFT_ALERT" : "CALIBRATED_LOCKED";
    if (driftEl) driftEl.textContent = `${driftFlag} (No Auto-Mutate)`;

    if (badgeEl) {
        const healthy = staleCount < MASTER_ASSETS.length && sc.rejectRatePct <= 25;
        badgeEl.textContent = healthy ? "HEALTHY" : "DEGRADED";
        badgeEl.className = healthy
            ? "px-1.5 py-0.2 rounded bg-[#00d09c]/15 text-[#059669] font-black"
            : "px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-700 font-black";
    }
}

function exportGovernanceAuditTrail() {
    const payload = {
        exportedAt: new Date().toISOString(),
        traderUid: SESSION_VAULT.activeUser?.traderUid || "ANONYMOUS",
        liveTradingFlag: LIVE_AUTO_TRADING_ENABLED,
        brokerLinked: { shoonya: SESSION_VAULT.shoonyaLinked, coindcx: SESSION_VAULT.coindcxLinked },
        scannerCoverage: SCANNER_COVERAGE,
        serverRiskState: SESSION_VAULT.serverRiskState,
        executionQualityLog: QUANT_FEATURE_STORE.executionLog,
        attributionByRegime: QUANT_FEATURE_STORE.attributionByRegime,
        anomalyLog: QUANT_FEATURE_STORE.anomalyLog,
        adaptiveScorecard: QUANT_FEATURE_STORE.adaptiveScorecard,
        recentSessionTrades: SESSION_VAULT.tradeHistory
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ARAF_AUDIT_${getIndiaTradingDate()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

function switchTab(name) {
    // Update browser history for SPA navigation
    window.history.pushState({ tab: name }, `ARAF - ${name}`, `?tab=${name}`);
    if (
        SESSION_VAULT.activeUser
        && SESSION_VAULT.activeUser.isActive !== false
        && (SESSION_VAULT.activeUser.kycStatus === "APPROVED"
            || SESSION_VAULT.activeUser.kycStatus === "VERIFIED_COMPLIANT")
        && !isSubscriptionActive(SESSION_VAULT.activeUser)
    ) {
        showSubscriptionPaywall();
        return;
    }
    const tabMap = {
        Home: "tabContentHome",
        Chart: "tabContentChart",
        Trade: "tabContentTrade",
        NewsAi: "tabContentNewsAi",
        ShoonyaPos: "tabContentPosition",
        Holdings: "tabContentHoldings",
        Ipo: "tabContentIpo",
        Settings: "tabContentSettings"
    };

    Object.entries(tabMap).forEach(([key, contentId]) => {
        const tabEl = document.getElementById(contentId);
        const navEl = document.getElementById(`navBtn${key}`);
        if (tabEl) tabEl.classList.add("hidden");
        if (navEl) navEl.className = "flex flex-col items-center justify-center text-slate-400 hover:text-slate-900";
    });

    const targetContent = document.getElementById(tabMap[name] || `tabContent${name}`);
    if (targetContent) targetContent.classList.remove("hidden");

    const activeNav = document.getElementById(`navBtn${name}`);
    if (activeNav) activeNav.className = "flex flex-col items-center justify-center text-[#00d09c] font-bold";

    if (name === "ShoonyaPos") {
        syncRealBrokerBalances();
        renderTradeHistory();
        initSyncPositionsToMemory();
        syncBrokerHoldingsAndOrders();
        syncServerRiskState();
        handleOrderSymbolChange();
    }
    if (name === "Holdings") {
        renderHoldingsItems();
        syncBrokerHoldingsAndOrders();
    }
    if (name === "NewsAi" && allFetchedNewsItems.length === 0) fetchRealFinancialNews();
    if (name === "Ipo") {
        fetchRealIpoData();
        fetchRealIpoNews();
        syncAutoTradeState();
    }
    if (name === "Chart") initTradingViewChart(currentSelectedAsset?.symbol || "NSE:NIFTY", currentSelectedAsset?.yahooSymbol || "");
    if (name === "Settings") {
        updateVaultStatusBadges();
        syncRealBrokerBalances();
        syncServerRiskState();
        checkSettingsRiskGuardVisibility();
    }

    window.scrollTo({ top: 0, behavior: "smooth" });
}

function isNseHoliday(date = new Date()) {
    const istDay = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Kolkata", weekday: "short" }).format(date);
    if (istDay === "Sat" || istDay === "Sun") return true;
    const istDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(date);
    return NSE_TRADING_HOLIDAYS.includes(istDate);
}

async function getIndiaMarketStatus() {
    try {
        const res = await fastApiRequest("/market/status");
        if (res.ok && res.data) {
            return res.data;
        }
    } catch (e) {
        console.warn("Failed to fetch market status:", e);
    }
    return null;
}

function getExchangeSessionStatus(category, now = new Date()) {
    if (category === "crypto" || category === "commodity") {
        return { isOpen: true, state: "OPEN_24X7", exchange: "CRYPTO", reason: "24/7 Active Market" };
    }
    if (isNseHoliday(now)) {
        return { isOpen: false, state: "CLOSED_HOLIDAY", exchange: "NSE/BSE", reason: "Weekend or Exchange Holiday" };
    }
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Kolkata", hour12: false, hour: "numeric", minute: "numeric"
    }).formatToParts(now);
    const hr = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const min = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    const totalMin = (hr * 60) + min;

    if (totalMin >= 540 && totalMin < 555) {
        return { isOpen: false, state: "PRE_OPEN", exchange: "NSE/BSE", reason: "Pre-Open Auction (09:00-09:15 IST)" };
    }
    if (totalMin >= 555 && totalMin <= 930) {
        return { isOpen: true, state: "OPEN_INTRADAY", exchange: "NSE/BSE", reason: "Regular Session Active" };
    }
    return { isOpen: false, state: "CLOSED_SESSION", exchange: "NSE/BSE", reason: "Outside Market Hours (09:15-15:30 IST)" };
}

function togglePasswordVisibility(inputId, btnEl) {
    const inp = document.getElementById(inputId);
    if (!inp) return;
    const isPass = inp.type === "password";
    inp.type = isPass ? "text" : "password";
    const icon = btnEl?.querySelector("i");
    if (icon) icon.className = isPass ? "fa-solid fa-eye-slash text-xs" : "fa-solid fa-eye text-xs";
}

function computeRealBlackScholes(spotPrice) {
    if (!Number.isFinite(spotPrice) || spotPrice <= 0) return;
    currentNiftyPrice = spotPrice;
}

function handleOrderSymbolChange() {
    const symEl = document.getElementById("tradeOrderSymbol");
    const qtyEl = document.getElementById("tradeOrderQty");
    const badgeEl = document.getElementById("orderRoutingBadge");
    if (!symEl) return;

    const sym = symEl.value;
    const spec = EXCHANGE_LOT_SPECS[sym];
    const canonical = resolveCanonicalInstrument(sym);

    if (qtyEl && spec) {
        qtyEl.step = String(spec.step);
        qtyEl.min = String(spec.min);
        if (qtyEl.dataset.lastSym !== sym) {
            qtyEl.value = String(spec.lot);
            qtyEl.dataset.lastSym = sym;
        }
    }

    if (badgeEl && canonical) {
        const linked = canonical.broker === "SHOONYA" ? SESSION_VAULT.shoonyaLinked : SESSION_VAULT.coindcxLinked;
        badgeEl.textContent = `${canonical.broker} • ${linked ? "LINKED" : "Connect API Key"}`;
        badgeEl.className = linked
            ? "text-[9px] bg-[#00d09c]/10 text-[#059669] font-mono font-bold px-2 py-0.5 rounded border border-[#00d09c]/30"
            : "text-[9px] bg-amber-500/10 text-amber-600 font-mono font-bold px-2 py-0.5 rounded border border-amber-500/30";
    }

    updateCryptoMarginCalculation();
    calculateSafeQtySuggestion();
}

function switchNewsAiSubTab(sub) {
    const isAi = sub === "ai";
    document.getElementById("subTabContent_ai")?.classList.toggle("hidden", !isAi);
    document.getElementById("subTabContent_news")?.classList.toggle("hidden", isAi);

    const bAi = document.getElementById("subTabBtn_ai");
    const bNews = document.getElementById("subTabBtn_news");
    if (bAi) bAi.className = isAi ? "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm" : "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
    if (bNews) bNews.className = !isAi ? "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm" : "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";

    if (!isAi && allFetchedNewsItems.length === 0) fetchRealFinancialNews();
}

function isGeopoliticalOrMacro(title) {
    const low = String(title || "").toLowerCase();
    return GEOPOLITICAL_KEYWORDS.some(k => low.includes(k));
}

function isNewsRelatedToAsset(title, asset) {
    if (!asset) return false;
    const low = String(title || "").toLowerCase();
    const baseKeywords = Array.isArray(asset.keywords) ? asset.keywords : [];
    const dynamicKeywords = [
        asset.orderKey,
        asset.name,
        asset.symbol,
        ...String(asset.name || "").split(/\s+/)
    ].filter(Boolean).flatMap(value => {
        const clean = String(value).replace(/[^a-zA-Z0-9 &.-]/g, "").trim();
        return clean ? [clean, clean.split(/[:\s/&.-]+/)[0]] : [];
    });
    const keywords = [...new Set([...baseKeywords, ...dynamicKeywords])]
        .map(k => String(k).toLowerCase().trim())
        .filter(k => k.length >= 3);
    return keywords.some(k => low.includes(k));
}

function switchNewsTabCategory(cat) {
    activeNewsTab = cat;
    ["stock_only", "geopolitical"].forEach(c => {
        const btn = document.getElementById(`newsTab_${c}`);
        if (btn) btn.className = c === cat ? "flex-1 py-1.5 rounded-xl bg-white text-slate-900 font-black transition shadow-sm" : "flex-1 py-1.5 rounded-xl text-slate-600 hover:text-slate-900 transition";
    });
    renderFilteredNews();
}

function toggleNewsVoiceDigest() {
    newsVoiceDigestEnabled = !newsVoiceDigestEnabled;
    terminalPreferences.news_voice_digest_enabled = newsVoiceDigestEnabled;
    void saveTerminalPreferences();
    const btn = document.getElementById("btnNewsVoiceToggle");
    if (btn) btn.innerHTML = `<i class="fa-solid ${newsVoiceDigestEnabled ? "fa-volume-high" : "fa-volume-xmark"}"></i> Voice: ${newsVoiceDigestEnabled ? "ON" : "OFF"}`;
    if (!newsVoiceDigestEnabled) stopAiVoice();
}

function speakSelectedAssetAudioDigest() {
    if (!newsVoiceDigestEnabled) { alert("News Voice Digest is OFF."); return; }
    const asset = currentSelectedAsset || MASTER_ASSETS[0];
    const related = allFetchedNewsItems.filter(n => isNewsRelatedToAsset(n.title, asset) || isGeopoliticalOrMacro(n.title)).slice(0, 3);
    const summary = related.length > 0
        ? related.map(r => r.translatedTitle || r.title).join(". ")
        : `${asset.name} catalyst wire has no breaking alerts right now.`;
    explainTextFriendly(summary, `${asset.name} Digest`);
}

async function translateClientSideFallback(text, targetLang) {
    if (!text || targetLang === "en") return text;
    try {
        const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=${encodeURIComponent(targetLang)}&dt=t&q=${encodeURIComponent(text.slice(0, 450))}`;
        const res = await fetch(url);
        if (!res.ok) return text;
        const data = await res.json();
        const translated = Array.isArray(data?.[0]) ? data[0].map(x => x?.[0] || "").join("") : "";
        return translated && translated.trim().toLowerCase() !== text.trim().toLowerCase() ? translated.trim() : text;
    } catch (e) {
        return text;
    }
}

async function setNewsDigestLanguage(langCode) {
    SESSION_VAULT.newsLang = langCode;
    terminalPreferences.news_lang = langCode;
    void saveTerminalPreferences();
    const shortMap = { "en-US": "en", "bn-IN": "bn", "hi-IN": "hi", "gu-IN": "gu", "mr-IN": "mr", "ta-IN": "ta" };
    Object.entries(shortMap).forEach(([full, s]) => {
        const btn = document.getElementById(`btnNewsLang_${s}`);
        if (btn) btn.className = full === langCode ? "px-2 py-0.5 rounded bg-[#00d09c] text-white font-black" : "px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-bold";
    });

    const tl = shortMap[langCode] || "en";
    if (tl !== "en" && allFetchedNewsItems.length > 0) {
        // Use new backend /news/translate endpoint with Gemini Flash
        const itemsToTranslate = allFetchedNewsItems.slice(0, 20).map(item => ({
            title: item.title,
            description: item.description,
            summary: item.summary
        }));
        const res = await fastApiRequest("/news/translate", {
            method: "POST",
            body: { items: itemsToTranslate, target_langs: [tl], fields: ["title", "description", "summary"] }
        });
        if (res.ok && Array.isArray(res.data?.items)) {
            res.data.items.forEach((tr, idx) => {
                if (allFetchedNewsItems[idx]) {
                    if (tr[`title_${tl}`]) allFetchedNewsItems[idx].translatedTitle = tr[`title_${tl}`];
                    if (tr[`description_${tl}`]) allFetchedNewsItems[idx].translatedDescription = tr[`description_${tl}`];
                    if (tr[`summary_${tl}`]) allFetchedNewsItems[idx].translatedSummary = tr[`summary_${tl}`];
                }
            });
        }
    } else {
        allFetchedNewsItems.forEach(i => { 
            delete i.translatedTitle; 
            delete i.translatedDescription; 
            delete i.translatedSummary; 
        });
    }
    renderFilteredNews();
}

async function fetchRealFinancialNews() {
    newsFetchState = "LOADING";
    const listEl = document.getElementById("newsFeedList");
    const badgeEl = document.getElementById("newsFilterCountBadge");
    if (listEl) listEl.innerHTML = `<div class="groww-card p-4 text-center text-slate-500 font-mono text-[10px]">Fetching verified catalyst wire...</div>`;

    const res = await fastApiRequest("/news/feed");
    if (res.ok && res.data?.success && Array.isArray(res.data.items)) {
        allFetchedNewsItems = res.data.items;
        newsFetchState = "LOADED";
        if (SESSION_VAULT.newsLang && SESSION_VAULT.newsLang !== "en-US") {
            await setNewsDigestLanguage(SESSION_VAULT.newsLang);
        } else {
            renderFilteredNews();
        }
    } else {
        newsFetchState = "UNAVAILABLE";
        if (badgeEl) badgeEl.textContent = "Catalyst Wire Unavailable";
        if (listEl) listEl.innerHTML = `<div class="groww-card p-4 text-center text-[#eb5b3c] font-mono text-[10px]">Failed to load live news feed.</div>`;
    }
}

let activeNewsModalFullText = "";
let activeNewsModalTitle = "";
let AI_CHAT_HISTORY = [];

function renderFilteredNews() {
    const listEl = document.getElementById("newsFeedList");
    const badgeEl = document.getElementById("newsFilterCountBadge");
    if (!listEl) return;

    const asset = currentSelectedAsset || MASTER_ASSETS[0];
    let filtered = allFetchedNewsItems.filter(n => {
        const parsedTime = n.pubDate ? Date.parse(n.pubDate) : NaN;
        if (Number.isFinite(parsedTime) && parsedTime < Date.now() - 72 * 60 * 60 * 1000) return false;
        if (activeNewsTab === "geopolitical") return isGeopoliticalOrMacro(n.title);
        return isNewsRelatedToAsset(n.title, asset);
    });
    
    // Sort: breaking news first, then by date
    filtered.sort((a, b) => {
        if (a.breaking && !b.breaking) return -1;
        if (!a.breaking && b.breaking) return 1;
        return (Date.parse(b.pubDate || 0)) - (Date.parse(a.pubDate || 0));
    });

    if (activeNewsTab !== "geopolitical" && filtered.length === 0) {
        badgeEl.textContent = "No stock-specific news available";
        listEl.innerHTML = `<div class="groww-card p-4 text-center text-slate-500 font-mono text-[10px]">No verified recent news matches ${sanitizeCleanText(asset.name || asset.orderKey)}.</div>`;
        return;
    }

    if (badgeEl) badgeEl.textContent = `Showing ${filtered.length} Catalyst Headlines`;
    if (filtered.length === 0) {
        listEl.innerHTML = `<div class="groww-card p-4 text-center text-slate-500 font-mono text-[10px]">No news catalysts found.</div>`;
        return;
    }

    // Get current language for translations
    const shortMap = { "en-US": "en", "bn-IN": "bn", "hi-IN": "hi", "gu-IN": "gu", "mr-IN": "mr", "ta-IN": "ta" };
    const tl = shortMap[SESSION_VAULT.newsLang] || "en";

    listEl.innerHTML = "";
    filtered.forEach((n) => {
        const card = document.createElement("div");
        const isBreaking = n.breaking === true;
        card.className = `groww-card p-3 space-y-1.5 bg-white cursor-pointer hover:border-[#00d09c] ${isBreaking ? "border-l-4 border-[#eb5b3c] bg-red-50/30" : ""}`;
        card.addEventListener("click", () => openNewsReaderModal(n));

        const top = document.createElement("div");
        top.className = "flex items-center justify-between text-[8.5px] font-mono text-slate-500";
        const src = document.createElement("span");
        const isOfficial = n.trustTier === "OFFICIAL";
        const isRumor = n.trustTier === "LOW_TRUST_RUMOR";
        src.className = isOfficial
            ? "px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-700 font-black"
            : (isRumor ? "px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-700 font-bold" : "px-1.5 py-0.5 rounded bg-[#00d09c]/10 text-[#059669] font-bold");
        src.textContent = `${n.verifiedSource || "WIRE"} • ${n.trustTier || "MEDIA"} (${n.eventCategory || "MARKET"})`;
        
        // Breaking badge
        if (isBreaking) {
            const breakingBadge = document.createElement("span");
            breakingBadge.className = "px-1.5 py-0.5 rounded bg-[#eb5b3c]/20 text-[#eb5b3c] font-black text-[8px] animate-pulse";
            breakingBadge.textContent = "🔴 BREAKING";
            top.append(src, breakingBadge);
        } else {
            top.append(src);
        }
        
        const tm = document.createElement("span");
        const parsedDate = n.pubDate ? new Date(n.pubDate) : null;
        tm.textContent = parsedDate && Number.isFinite(parsedDate.getTime())
            ? parsedDate.toLocaleString(SESSION_VAULT.newsLang === "bn-IN" ? "bn-IN" : "en-IN", {
                day: "2-digit",
                month: "short",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                timeZone: "Asia/Kolkata"
            })
            : "";
        top.append(tm);

        const titleP = document.createElement("p");
        titleP.className = "text-[11px] font-bold text-slate-900 leading-snug";
        
        // Use pre-translated content if available
        const useTranslated = tl !== "en";
        const displayTitle = useTranslated && n[`title_${tl}`] ? n[`title_${tl}`] : n.title;
        const displaySummary = useTranslated && n[`summary_${tl}`] ? n[`summary_${tl}`] : (n.summary || n.title);
        
        const impactTag = n.eventCategory && n.eventCategory !== "MARKET" ? `<span class="inline-block text-[8px] rounded bg-slate-100/20 text-slate-400 px-1 py-0.5 mx-1">📊 ${n.eventCategory}</span>` : "";
        titleP.innerHTML = `${impactTag}${displayTitle}. ${displaySummary}`;

        const hint = document.createElement("span");
        hint.className = "inline-block text-[9px] text-[#059669] font-mono font-bold";
        hint.textContent = "📖 Read Full Story In-App →";

        card.append(top, titleP, hint);
        listEl.appendChild(card);
    });
}

async function openNewsReaderModal(newsItem) {
    const modal = document.getElementById("newsReaderModal");
    const srcEl = document.getElementById("newsModalSource");
    const titleEl = document.getElementById("newsModalTitle");
    const bodyEl = document.getElementById("newsModalBody");
    if (!modal || !bodyEl) return;

    const shortMap = { "en-US": "en", "bn-IN": "bn", "hi-IN": "hi", "gu-IN": "gu", "mr-IN": "mr", "ta-IN": "ta" };
    const tl = shortMap[SESSION_VAULT.newsLang] || "en";

    if (srcEl) srcEl.textContent = newsItem.verifiedSource || "MARKET WIRE";
    
    // Use pre-translated content from feed
    const useTranslated = tl !== "en";
    const displayTitle = useTranslated && newsItem[`title_${tl}`] ? newsItem[`title_${tl}`] : newsItem.title;
    const displayBody = useTranslated && newsItem[`description_${tl}`] ? newsItem[`description_${tl}`] : newsItem.description;
    const displaySummary = useTranslated && newsItem[`summary_${tl}`] ? newsItem[`summary_${tl}`] : newsItem.summary;
    
    if (titleEl) titleEl.textContent = displayTitle;
    bodyEl.innerHTML = `<div class="py-6 text-center text-slate-500 font-mono text-xs"><i class="fa-solid fa-spinner animate-spin text-[#00d09c] mr-2"></i> Loading Full Story...</div>`;
    modal.classList.remove("hidden");

    // Build full article from pre-translated fields
    const fullContent = [displayTitle, displaySummary, displayBody].filter(Boolean).join(". ");
    
    if (fullContent) {
        activeNewsModalTitle = displayTitle;
        activeNewsModalFullText = fullContent;
        if (titleEl) titleEl.textContent = displayTitle;
        bodyEl.innerHTML = "";
        
        // Split into paragraphs
        const paras = fullContent.split(". ").filter(p => p.trim().length > 20);
        paras.forEach(pText => {
            const p = document.createElement("p");
            p.className = "text-slate-700 leading-relaxed";
            p.textContent = pText.trim() + ".";
            bodyEl.appendChild(p);
        });
    } else {
        activeNewsModalTitle = displayTitle;
        activeNewsModalFullText = displayTitle;
        bodyEl.innerHTML = `<p class="text-slate-700">${sanitizeCleanText(displayTitle)}</p>`;
    }
}

function closeNewsReaderModal() {
    document.getElementById("newsReaderModal")?.classList.add("hidden");
    stopAiVoice();
}

function speakCurrentNewsModal() {
    if (!activeNewsModalFullText) return;
    const prevLang = SESSION_VAULT.voiceLang;
    SESSION_VAULT.voiceLang = SESSION_VAULT.newsLang || prevLang;
    explainTextFriendly(activeNewsModalFullText.slice(0, 600), activeNewsModalTitle || "News Reader");
    SESSION_VAULT.voiceLang = prevLang;
}
window.AUTO_TRADE_STATE = {
    enabled: false,
    dailyBudgetInr: 10000,
    usedBudgetInr: 0,
    reservedBudgetInr: 0,
    activeAutoPositions: 0,
    maxOpenAutoPositions: 3,
    allowedMarkets: "ALL",
    auditTrail: []
};
let activeIpoNewsLang = "bn";
let autoTradeExecutionLog = [];

function switchIpoAutoSubTab(sub) {
    const isIpo = sub === "ipo";
    document.getElementById("subTabContent_ipo")?.classList.toggle("hidden", !isIpo);
    document.getElementById("subTabContent_autotrade")?.classList.toggle("hidden", isIpo);
    const bIpo = document.getElementById("subTabBtn_ipo");
    const bAuto = document.getElementById("subTabBtn_autotrade");
    if (bIpo) bIpo.className = isIpo ? "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm" : "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
    if (bAuto) bAuto.className = !isIpo ? "flex-1 py-2 rounded-xl bg-white text-slate-900 font-black transition shadow-sm" : "flex-1 py-2 rounded-xl text-slate-600 hover:text-slate-900 transition";
    if (!isIpo) {
        syncAutoTradeState();
        startPaperPnLPolling();
        startAISwarmTelemetryPolling();
    } else {
        stopPaperPnLPolling();
        stopAISwarmTelemetryPolling();
    }
}

function updateAutoTradeRouteBadge() {
    const badge = document.getElementById("autoTradeDataRouteBadge");
    if (!badge) return;
    const indSrc = SESSION_VAULT.shoonyaLinked ? "SHOONYA" : "YAHOO";
    const crySrc = SESSION_VAULT.coindcxLinked ? "COINDCX" : "BINANCE";
    badge.textContent = `IND:${indSrc} • CRY:${crySrc}`;
}

function renderAutoTradeUi() {
    const st = window.AUTO_TRADE_STATE || {};
    const riskLocked = !serverRiskStateLoaded
        || riskPnlSyncFailed
        || SESSION_VAULT.serverRiskState?.isHardLocked === true;
    
    // Crypto auto-trade state
    const cryptoConfigured = st.crypto_enabled === true
        && st.globalEnabled === true
        && st.globalKillSwitch !== true
        && !riskLocked;
    const cryptoGated = cryptoConfigured && st.paperLifecycleReady !== true;
    const cryptoOn = cryptoConfigured && st.paperLifecycleReady === true;
    
    // India auto-trade state
    const indiaConfigured = st.india_enabled === true
        && st.globalEnabled === true
        && st.globalKillSwitch !== true
        && !riskLocked;
    const indiaGated = indiaConfigured && st.paperLifecycleReady !== true;
    const indiaOn = indiaConfigured && st.paperLifecycleReady === true;
    
    // Overall state (for backward compatibility with top pill)
    const anyConfigured = cryptoConfigured || indiaConfigured;
    const anyOn = cryptoOn || indiaOn;
    const anyGated = cryptoGated || indiaGated;

    const topPill = document.getElementById("autoTradeTopPill");
    const badge = document.getElementById("autoTradeSwitchStatusBadge");
    
    // Crypto toggle buttons
    const cryptoBtnOn = document.getElementById("btnCryptoAutoTradeOn");
    const cryptoBtnOff = document.getElementById("btnCryptoAutoTradeOff");
    const cryptoBadge = document.getElementById("cryptoAutoTradeStatusBadge");
    
    // India toggle buttons
    const indiaBtnOn = document.getElementById("btnIndiaAutoTradeOn");
    const indiaBtnOff = document.getElementById("btnIndiaAutoTradeOff");
    const indiaBadge = document.getElementById("indiaAutoTradeStatusBadge");

    if (topPill) {
        topPill.textContent = riskLocked ? "AUTO: RISK LOCKED" : anyOn ? "AUTO: ON" : anyGated ? "AUTO: GATED" : "AUTO: OFF";
        topPill.className = anyOn
            ? "text-[8px] bg-[#00d09c] text-white font-mono px-2 py-0.5 rounded font-black"
            : anyGated
                ? "text-[8px] bg-amber-100 text-amber-700 font-mono px-2 py-0.5 rounded font-bold border border-amber-300"
            : "text-[8px] bg-slate-100 text-slate-600 font-mono px-2 py-0.5 rounded font-bold border border-slate-200";
    }
    if (badge) {
        badge.textContent = anyOn ? "ON (ACTIVE)" : anyGated ? "ON (LIFECYCLE BLOCKED)" : "OFF";
        badge.className = anyOn
            ? "px-2.5 py-1 rounded-lg bg-[#00d09c]/15 text-[#059669] font-mono font-black text-[10px]"
            : anyGated
                ? "px-2.5 py-1 rounded-lg bg-amber-100 text-amber-700 font-mono font-black text-[10px]"
            : "px-2.5 py-1 rounded-lg bg-[#eb5b3c]/15 text-[#eb5b3c] font-mono font-black text-[10px]";
    }

    // Crypto toggle UI
    if (cryptoBtnOn) {
        cryptoBtnOn.disabled = riskLocked || st.globalKillSwitch === true || !serverAutoTradeStateLoaded;
        cryptoBtnOn.className = cryptoOn ? "py-2 rounded-xl groww-btn-green text-white shadow-md transition active:scale-95" : "py-2 rounded-xl bg-slate-100 text-slate-600 border border-slate-200 transition active:scale-95";
    }
    if (cryptoBtnOff) cryptoBtnOff.className = !cryptoOn ? "py-2 rounded-xl bg-[#eb5b3c] text-white shadow-md transition active:scale-95" : "py-2 rounded-xl bg-slate-100 text-slate-600 border border-slate-200 transition active:scale-95";
    if (cryptoBadge) {
        cryptoBadge.textContent = cryptoOn ? "ON (ACTIVE)" : cryptoGated ? "ON (LIFECYCLE BLOCKED)" : "OFF";
        cryptoBadge.className = cryptoOn
            ? "px-2.5 py-1 rounded-lg bg-[#00d09c]/15 text-[#059669] font-mono font-black text-[10px]"
            : cryptoGated
                ? "px-2.5 py-1 rounded-lg bg-amber-100 text-amber-700 font-mono font-black text-[10px]"
            : "px-2.5 py-1 rounded-lg bg-[#eb5b3c]/15 text-[#eb5b3c] font-mono font-black text-[10px]";
    }

    // India toggle UI
    if (indiaBtnOn) {
        indiaBtnOn.disabled = riskLocked || st.globalKillSwitch === true || !serverAutoTradeStateLoaded;
        indiaBtnOn.className = indiaOn ? "py-2 rounded-xl groww-btn-green text-white shadow-md transition active:scale-95" : "py-2 rounded-xl bg-slate-100 text-slate-600 border border-slate-200 transition active:scale-95";
    }
    if (indiaBtnOff) indiaBtnOff.className = !indiaOn ? "py-2 rounded-xl bg-[#eb5b3c] text-white shadow-md transition active:scale-95" : "py-2 rounded-xl bg-slate-100 text-slate-600 border border-slate-200 transition active:scale-95";
    if (indiaBadge) {
        indiaBadge.textContent = indiaOn ? "ON (ACTIVE)" : indiaGated ? "ON (LIFECYCLE BLOCKED)" : "OFF";
        indiaBadge.className = indiaOn
            ? "px-2.5 py-1 rounded-lg bg-[#00d09c]/15 text-[#059669] font-mono font-black text-[10px]"
            : indiaGated
                ? "px-2.5 py-1 rounded-lg bg-amber-100 text-amber-700 font-mono font-black text-[10px]"
            : "px-2.5 py-1 rounded-lg bg-[#eb5b3c]/15 text-[#eb5b3c] font-mono font-black text-[10px]";
    }

    // Market status banner
    const marketBanner = document.getElementById("autoTradeMarketBanner");
    if (marketBanner) {
        const indSess = getExchangeSessionStatus("indian");
        const scope = st.allowedMarkets || "ALL";
        const showsIndia = scope === "ALL" || scope === "INDIA";
        if (showsIndia && !indSess.isOpen) {
            marketBanner.classList.remove("hidden");
            marketBanner.innerHTML = `
                <div class="flex items-center justify-between p-2 bg-amber-50 border border-amber-200 rounded-xl text-amber-800">
                    <div class="flex items-center gap-2">
                        <i class="fa-solid fa-circle-exclamation text-amber-500"></i>
                        <div>
                            <b class="text-[10px]">Indian Market ${indSess.state === "CLOSED_HOLIDAY" ? "Closed (Holiday)" : indSess.state === "CLOSED_WEEKEND" ? "Closed (Weekend)" : "Closed"}</b>
                            <span class="block text-[9px] text-amber-700">${indSess.reason}</span>
                        </div>
                    </div>
                    <span class="text-[9px] font-mono text-amber-600">Next Open: ${indSess.next_open_ist ? new Date(indSess.next_open_ist).toLocaleString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "--"} IST</span>
                </div>`;
        } else {
            marketBanner.classList.add("hidden");
        }
    }

    const budget = Number(st.dailyBudgetInr || 10000);
    const used = Number(st.usedBudgetInr || 0) + Number(st.reservedBudgetInr || 0);
    const left = Math.max(0, budget - used);

    // Risk guard details
    const riskState = SESSION_VAULT.serverRiskState || {};
    const tradesToday = Number(riskState.tradesToday || 0);
    const maxDailyTrades = Number(riskState.maxDailyTrades || 5);
    const dailyPnlInr = Number(riskState.dailyPnlInr || 0);
    const dailyLossLimit = Number(riskState.dailyLossLimitInr || 0);
    const dailyLossPct = dailyLossLimit > 0 ? ((Math.abs(dailyPnlInr) / dailyLossLimit) * 100).toFixed(1) : 0;

    const remBadge = document.getElementById("autoTradeRemainingBadge");
    const inpBudget = document.getElementById("autoTradeDailyBudgetInput");
    const selScope = document.getElementById("autoTradeMarketScope");
    const elLim = document.getElementById("atStatLimit");
    const elUsed = document.getElementById("atStatUsed");
    const elPos = document.getElementById("atStatPos");
    const elTrades = document.getElementById("atStatTrades");
    const elDailyPnl = document.getElementById("atStatDailyPnl");

    if (remBadge) remBadge.textContent = `Left: ₹${Math.round(left).toLocaleString("en-IN")}`;
    if (inpBudget && document.activeElement !== inpBudget) inpBudget.value = String(budget);
    if (selScope && st.allowedMarkets) selScope.value = st.allowedMarkets;
    if (elLim) elLim.textContent = `₹${Math.round(budget).toLocaleString("en-IN")}`;
    if (elUsed) elUsed.textContent = `₹${Math.round(used).toLocaleString("en-IN")}`;
    if (elPos) elPos.textContent = `${st.activeAutoPositions || 0} / ${st.maxOpenAutoPositions || 3}`;
    if (elTrades) elTrades.textContent = `${tradesToday} / ${maxDailyTrades}`;
    if (elDailyPnl) {
        const pnlColor = dailyPnlInr >= 0 ? "text-[#059669]" : "text-[#eb5b3c]";
        elDailyPnl.innerHTML = `<span class="${pnlColor}">₹${Math.round(dailyPnlInr).toLocaleString("en-IN")}</span> / ₹${Math.round(dailyLossLimit).toLocaleString("en-IN")} (${dailyLossPct}%)`;
    }
    updateAutoTradeRouteBadge();

    const logEl = document.getElementById("autoTradeExecutionLog");
    if (logEl) {
        if (!autoTradeExecutionLog.length) {
            logEl.innerHTML = `<p class="text-[9px] text-slate-500 font-mono text-center py-2">No auto-trade executions today.</p>`;
        } else {
            logEl.innerHTML = autoTradeExecutionLog.map(l => `
                <div class="p-2 rounded-xl bg-slate-50 border border-slate-200 text-[9.5px] font-mono flex items-center justify-between">
                    <div>
                        <b class="text-slate-900">${sanitizeCleanText(l.symbol)} (${sanitizeCleanText(l.side)})</b>
                        <span class="block text-[8px] text-slate-500">Qty:${l.qty} • Score:${l.score}/100 • SL:${l.sl} • TP:${l.tp}</span>
                    </div>
                    <span class="px-1.5 py-0.5 rounded text-[8px] font-black ${l.ok ? "bg-[#00d09c]/15 text-[#059669]" : "bg-[#eb5b3c]/15 text-[#eb5b3c]"}">${sanitizeCleanText(l.status)}</span>
                </div>
            `).join("");
        }
    }
}

async function syncAutoTradeState() {
    if (!auth.currentUser) return false;
    try {
        const res = await fastApiRequest("/automation/settings");
        if (!res.ok || !res.data?.automation) {
            throw new Error(res.data?.detail || `Automation status request failed (${res.status})`);
        }
        window.AUTO_TRADE_STATE = res.data.automation;
        serverAutoTradeStateLoaded = true;
        renderAutoTradeUi();
        await Promise.all([pollBackendOpportunities(), loadExecutionLog()]);
        return true;
    } catch (error) {
        serverAutoTradeStateLoaded = false;
        window.AUTO_TRADE_STATE = {
            ...window.AUTO_TRADE_STATE,
            enabled: false,
            globalEnabled: false,
            globalKillSwitch: true
        };
        console.error("Authoritative server kill-switch state could not be loaded", error);
        renderAutoTradeUi();
        return false;
    }
}

async function loadExecutionLog() {
    if (!auth.currentUser) return;
    try {
        const res = await fastApiRequest("/automation/execution-log", { method: "GET", query: "limit=100" });
        if (res.ok && Array.isArray(res.data?.logs)) {
            autoTradeExecutionLog = res.data.logs.map(l => ({
                symbol: l.symbol,
                side: l.side,
                qty: l.quantity,
                entry: l.entry_price,
                sl: l.stop_loss,
                tp: l.take_profit,
                score: l.score,
                status: l.status,
                ok: l.status === "CLAIMED" || l.status === "FILLED",
                isAuto: l.is_auto_trade,
                created_at: l.created_at
            }));
        }
    } catch (e) {
        console.warn("Failed to load execution log:", e);
    }
}

async function saveExecutionLogToBackend(entry) {
    if (!auth.currentUser) return;
    try {
        await fastApiRequest("/automation/execution-log", {
            method: "POST",
            body: entry
        });
    } catch (e) {
        console.warn("Failed to save execution log:", e);
    }
}

async function pollBackendOpportunities() {
    if (!auth.currentUser || !isTerminalUnlocked) return;
    const res = await fastApiRequest("/automation/opportunities");
    if (res.ok && Array.isArray(res.data?.opportunities)) {
        enqueueOpportunities(res.data.opportunities);
    }
}

async function runBackendAutomationScan() {
    if (!auth.currentUser || !isTerminalUnlocked || document.hidden || backendAutomationScanBusy) return;
    backendAutomationScanBusy = true;
    try {
        const st = window.AUTO_TRADE_STATE || {};
        const markets = [];
        
        // Check crypto toggle independently
        if (st.crypto_enabled === true) {
            markets.push("CRYPTO");
        }
        
        // Check india toggle independently
        if (st.india_enabled === true) {
            const indSess = getExchangeSessionStatus("indian");
            if (indSess.isOpen) {
                markets.push("INDIA");
            } else {
                console.log(`[Automation] Skipping INDIA scan - market ${indSess.state}: ${indSess.reason}`);
            }
        }
        
        // Also respect allowedMarkets scope as a filter
        const scope = st.allowedMarkets || "ALL";
        const filteredMarkets = markets.filter(m => scope === "ALL" || scope === m);
        
        if (filteredMarkets.length === 0) {
            console.log("[Automation] No markets enabled for scanning");
            return;
        }
        
        const responses = await Promise.all(filteredMarkets.map(market => fastApiRequest("/automation/scan", {
            method: "POST",
            body: { market, timeframe: "15m", limit: 10 },
            timeoutMs: 30000
        })));
        const opportunities = [];
        responses.forEach((response, index) => {
            if (response.ok && Array.isArray(response.data?.opportunities)) {
                opportunities.push(...response.data.opportunities);
            } else if (!response.ok) {
                console.warn(`[Automation Scan:${filteredMarkets[index]}] Server scan unavailable:`, response.data?.detail || response.status);
            }
        });
        enqueueOpportunities(opportunities);
    } finally {
        backendAutomationScanBusy = false;
    }
}

async function toggleCryptoAutoTrade(enableBool) {
    if (!checkProAccess()) return;
    if (enableBool && (
        !serverRiskStateLoaded
        || riskPnlSyncFailed
        || !serverAutoTradeStateLoaded
        || SESSION_VAULT.serverRiskState?.isHardLocked === true
        || window.AUTO_TRADE_STATE?.globalKillSwitch === true
    )) {
        alert("Server risk or kill-switch state blocks auto-trading.");
        return;
    }
    if (enableBool && !confirm("Enable Crypto paper auto-trading?\n\nRuns 24/7 using crypto-specific logic (volatility, funding/orderbook depth, crypto sentiment). Only server-validated opportunities scoring at least 90/100 can be considered. 70–89 scores remain manual alerts. Live broker execution remains disabled.")) return;

    const res = await fastApiRequest("/automation/settings", {
        method: "PUT",
        body: { crypto_enabled: enableBool }
    });
    if (res.ok && res.data?.automation) {
        window.AUTO_TRADE_STATE = { ...window.AUTO_TRADE_STATE, ...res.data.automation };
        renderAutoTradeUi();
    } else {
        alert(res.data?.detail || "Failed to update Crypto Auto Trade switch.");
    }
}

async function toggleIndiaAutoTrade(enableBool) {
    if (!checkProAccess()) return;
    if (enableBool && (
        !serverRiskStateLoaded
        || riskPnlSyncFailed
        || !serverAutoTradeStateLoaded
        || SESSION_VAULT.serverRiskState?.isHardLocked === true
        || window.AUTO_TRADE_STATE?.globalKillSwitch === true
    )) {
        alert("Server risk or kill-switch state blocks auto-trading.");
        return;
    }
    if (enableBool && !confirm("Enable India (Shoonya) paper auto-trading?\n\nRuns ONLY during Indian market hours (09:15–15:30 IST, weekdays, non-holidays) using Indian market logic (Nifty/BankNifty/Sensex, FII/DII flows, equity intraday). Only server-validated opportunities scoring at least 90/100 can be considered. 70–89 scores remain manual alerts. Live broker execution remains disabled.")) return;

    const res = await fastApiRequest("/automation/settings", {
        method: "PUT",
        body: { india_enabled: enableBool }
    });
    if (res.ok && res.data?.automation) {
        window.AUTO_TRADE_STATE = { ...window.AUTO_TRADE_STATE, ...res.data.automation };
        renderAutoTradeUi();
    } else {
        alert(res.data?.detail || "Failed to update India Auto Trade switch.");
    }
}

// Legacy function for backward compatibility - delegates to both
async function toggleAutoTradeSwitch(enableBool) {
    await Promise.all([
        toggleCryptoAutoTrade(enableBool),
        toggleIndiaAutoTrade(enableBool)
    ]);
}

async function saveAutoTradeBudgetConfig() {
    if (!checkProAccess()) return;
    const dailyBudgetInr = Number(document.getElementById("autoTradeDailyBudgetInput")?.value || 10000);
    const allowedMarkets = document.getElementById("autoTradeMarketScope")?.value || "ALL";
    if (!Number.isFinite(dailyBudgetInr) || dailyBudgetInr < 1000) {
        alert("Minimum Daily Auto Trade Limit is ₹1,000!");
        return;
    }
    const res = await fastApiRequest("/automation/settings", {
        method: "PUT",
        body: { daily_budget_inr: dailyBudgetInr, allowed_markets: allowedMarkets }
    });
    if (res.ok && res.data?.automation) {
        window.AUTO_TRADE_STATE = { ...window.AUTO_TRADE_STATE, ...res.data.automation };
        renderAutoTradeUi();
        alert(`✅ Daily Auto Limit Locked: ₹${Math.round(res.data.automation.dailyBudgetInr).toLocaleString("en-IN")} (${allowedMarkets})`);
    } else {
        alert(res.data?.detail || "Failed to lock Auto Trade budget.");
    }
}

async function fetchRealIpoData() {
    const container = document.getElementById("ipoFeedContainer");
    if (container) container.innerHTML = `<div class="groww-card p-4 text-center text-slate-500 font-mono text-[10px]"><i class="fa-solid fa-spinner animate-spin text-[#00d09c] mr-1"></i> Loading live IPO & GMP board...</div>`;
    
    const res = await secureApiCall("ipo_feed", { method: "GET" });
    if (res.ok && res.data?.success && Array.isArray(res.data.items)) {
        liveIpoList = res.data.items;
        
        // Update GMP feed status badge
        const statusBadge = document.getElementById("ipoGmpFeedBadge");
        if (statusBadge) {
            const status = res.data.gmpFeed || "unknown";
            const verifiedCount = res.data.gmpVerifiedCount || 0;
            const totalCount = res.data.gmpTotalCount || 0;
            let badgeClass = "bg-slate-100 text-slate-600";
            let badgeText = "GMP: Unknown";
            
            if (status === "verified") {
                badgeClass = "bg-[#00d09c]/15 text-[#059669]";
                badgeText = `GMP: Verified (${verifiedCount}/${totalCount})`;
            } else if (status === "healthy" || status === "single_source") {
                badgeClass = "bg-amber-100 text-amber-700";
                badgeText = `GMP: Single Source (${totalCount})`;
            } else if (status === "cached") {
                badgeClass = "bg-slate-100 text-slate-600";
                badgeText = "GMP: Cached";
            } else {
                badgeClass = "bg-[#eb5b3c]/15 text-[#eb5b3c]";
                badgeText = "GMP: Unavailable";
            }
            statusBadge.className = `px-2 py-0.5 rounded text-[8px] font-mono font-bold ${badgeClass}`;
            statusBadge.textContent = badgeText;
        }
        
renderIpoList();
    } else if (container) {
        container.innerHTML = `<div class="groww-card p-4 text-center text-[#eb5b3c] font-mono text-[10px]">IPO GMP feed temporarily unavailable.</div>`;
    }
}

function filterIpoType(type) {
    activeIpoFilter = type;
    ["all", "mainboard", "sme"].forEach(t => {
        const btn = document.getElementById(`ipoFilter_${t}`);
        if (btn) btn.className = t === type ? "px-3 py-1 rounded-xl bg-[#00d09c] text-white font-black shadow-md" : "px-3 py-1 rounded-xl bg-white border border-slate-200 text-slate-600";
    });
    renderIpoList();
}

function renderIpoList() {
    const container = document.getElementById("ipoFeedContainer");
    if (!container) return;
    const list = liveIpoList.filter(i => activeIpoFilter === "all" || i.type === activeIpoFilter);
    if (list.length === 0) {
        container.innerHTML = `<div class="groww-card p-4 text-center text-slate-500 font-mono text-[10px]">No IPOs found for selected filter.</div>`;
        return;
    }
    container.innerHTML = list.map(i => {
        const hist = Array.isArray(i.gmpHistory) ? i.gmpHistory : [];
        const maxG = hist.length ? Math.max(...hist.map(h => Math.abs(h.gmp) || 1), 1) : 1;
        const barsHtml = hist.length > 0
            ? `<div class="flex items-end space-x-1 h-7 pt-1">${hist.map(h => `<div title="${sanitizeCleanText(h.date)}: ₹${h.gmp}" class="flex-1 rounded-t ${h.gmp >= 0 ? "bg-[#00d09c]" : "bg-[#eb5b3c]"}" style="height:${Math.max(20, Math.round((Math.abs(h.gmp) / maxG) * 100))}%"></div>`).join("")}</div>`
            : `<span class="text-[8.5px] text-slate-400 font-mono">Tracking daily GMP history from today...</span>`;
        const updTxt = i.lastUpdatedAt ? new Date(i.lastUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "--";
        
        // GMP verified badge
        const gmpVerified = i.gmpVerified === true;
        const gmpSources = Array.isArray(i.gmpSources) ? i.gmpSources : [];
        const sourceBadge = gmpVerified 
            ? `<span class="px-1.5 py-0.5 rounded text-[7px] font-mono font-bold bg-[#00d09c]/15 text-[#059669] border border-[#00d09c]/30">✓ Verified (${gmpSources.join(",")})</span>`
            : (i.gmpSources && i.gmpSources.length > 0 
                ? `<span class="px-1.5 py-0.5 rounded text-[7px] font-mono font-bold bg-amber-100 text-amber-700">Source: ${i.gmpSources.join(",")}</span>`
                : `<span class="px-1.5 py-0.5 rounded text-[7px] font-mono font-bold bg-slate-100 text-slate-500">GMP: Single Source</span>`);

        // Subscription % badge
        const subPct = i.subscriptionPct !== undefined ? i.subscriptionPct : 0;
        const subPctBadge = subPct > 0 
            ? `<span class="px-1.5 py-0.5 rounded text-[7px] font-mono font-bold ${subPct >= 100 ? "bg-[#00d09c]/15 text-[#059669]" : subPct >= 50 ? "bg-blue-100 text-blue-700" : "bg-amber-100 text-amber-700"}">${subPct >= 100 ? "FULL" : subPct}%</span>`
            : `<span class="px-1.5 py-0.5 rounded text-[7px] font-mono font-bold bg-slate-100 text-slate-500">--%</span>`;

        return `
        <div class="groww-card p-3.5 space-y-2 bg-white">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <h4 class="text-xs font-black text-slate-900 heading-font">${sanitizeCleanText(i.name)}</h4>
                    ${gmpVerified ? `<span class="px-1.5 py-0.5 rounded text-[7px] font-mono font-bold bg-[#00d09c]/15 text-[#059669] border border-[#00d09c]/30">✓ Multi-source Verified</span>` : ""}
                </div>
                <div class="flex items-center gap-1.5">
                    ${sourceBadge}
                    ${subPctBadge}
                    <span class="text-[8px] px-2 py-0.5 rounded font-mono font-bold uppercase ${i.type === "sme" ? "bg-amber-500/15 text-amber-600" : "bg-[#00d09c]/15 text-[#059669]"}">${sanitizeCleanText(i.type)}</span>
                </div>
            </div>
            <div class="grid grid-cols-4 gap-2 bg-slate-50 p-2 rounded-xl border border-slate-200 text-[9.5px] mono-font">
                <div><span class="text-slate-500 block text-[8px]">Indicative GMP</span><b class="text-[#00d09c]">${sanitizeCleanText(i.gmp)}</b></div>
                <div><span class="text-slate-500 block text-[8px]">Price Band</span><b class="text-slate-900">${sanitizeCleanText(i.band)}</b></div>
                <div><span class="text-slate-500 block text-[8px]">Subscription</span>${subPctBadge}</div>
                <div class="text-right"><span class="text-slate-500 block text-[8px]">Lot Size</span><b class="text-blue-600">${sanitizeCleanText(i.sub)}</b></div>
            </div>
            <div class="bg-slate-50 px-2.5 py-1.5 rounded-xl border border-slate-200">
                <div class="flex items-center justify-between text-[8px] font-mono text-slate-500">
                    <span>GMP Trend (${hist.length} pts)</span>
                    <span>Updated: ${updTxt} • ${sanitizeCleanText(i.source || "Aggregated")}</span>
                </div>
                ${barsHtml}
            </div>
        </div>`;
    }).join("");
}

function setIpoNewsLang(tl) {
    activeIpoNewsLang = tl;
    ["bn", "en", "hi", "gu", "mr", "ta"].forEach(l => {
        const b = document.getElementById(`btnIpoNewsLang_${l}`);
        if (b) b.className = l === tl ? "px-2 py-0.5 rounded bg-[#00d09c] text-white font-black" : "px-2 py-0.5 rounded bg-slate-100 text-slate-600";
    });
    fetchRealIpoNews();
}

async function fetchRealIpoNews() {
    const box = document.getElementById("ipoNewsFeedContainer");
    if (!box) return;
    box.innerHTML = `<p class="text-[9.5px] text-slate-500 font-mono text-center py-3"><i class="fa-solid fa-spinner animate-spin text-[#00d09c] mr-1"></i> Loading IPO news in ${activeIpoNewsLang.toUpperCase()}...</p>`;

    const res = await secureApiCall("ipo_news_feed", {
        method: "GET",
        query: `tl=${encodeURIComponent(activeIpoNewsLang)}`
    });
    if (!res.ok || !res.data?.success || !Array.isArray(res.data.items) || res.data.items.length === 0) {
        box.innerHTML = `<p class="text-[9.5px] text-[#eb5b3c] font-mono text-center py-2">IPO news feed unavailable right now.</p>`;
        return;
    }

    box.innerHTML = "";
    res.data.items.forEach(item => {
        const row = document.createElement("div");
        row.className = "p-2.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1 cursor-pointer hover:border-[#00d09c]";
        row.addEventListener("click", () => {
            const prevLang = SESSION_VAULT.newsLang;
            const langMap = { bn: "bn-IN", en: "en-US", hi: "hi-IN", gu: "gu-IN", mr: "mr-IN", ta: "ta-IN" };
            SESSION_VAULT.newsLang = langMap[activeIpoNewsLang] || "bn-IN";
            openNewsReaderModal(item);
            SESSION_VAULT.newsLang = prevLang;
        });

        const top = document.createElement("div");
        top.className = "flex items-center justify-between text-[8px] font-mono";
        const badge = document.createElement("span");
        badge.className = item.trustTier === "OFFICIAL"
            ? "px-1.5 py-0.2 rounded bg-blue-500/15 text-blue-700 font-black"
            : (item.trustTier === "LOW_TRUST_RUMOR" ? "px-1.5 py-0.2 rounded bg-amber-500/15 text-amber-700 font-bold" : "px-1.5 py-0.2 rounded bg-[#00d09c]/15 text-[#059669] font-bold");
        badge.textContent = `${item.verifiedSource} • ${item.trustTier === "LOW_TRUST_RUMOR" ? "GREY/RUMOR" : item.trustTier}`;
        const dt = document.createElement("span");
        dt.className = "text-slate-400";
        const ipoDate = item.pubDate ? new Date(item.pubDate) : null;
        dt.textContent = ipoDate && Number.isFinite(ipoDate.getTime())
            ? ipoDate.toLocaleString(activeIpoNewsLang === "bn" ? "bn-IN" : "en-IN", {
                day: "2-digit",
                month: "short",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                timeZone: "Asia/Kolkata"
            })
            : "";
        top.append(badge, dt);

        const titleP = document.createElement("p");
        titleP.className = "text-[10.5px] font-bold text-slate-900 leading-snug";
        titleP.textContent = item.translatedTitle || item.title;

        row.append(top, titleP);
        box.appendChild(row);
    });
}

window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
});

async function installPwaApp() {
    if (deferredPrompt) {
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
    } else {
        alert("To install: tap your browser menu (⋮ or Share) and choose 'Add to Home Screen'.");
    }
}

function bootArafTerminal() {
    loadLocalTerminalPreferences();
    loadLocalRiskConfig();
    void startSplashScreen();
    loadNseHolidays();

    // Mobile/browser back button: close modals or navigate tabs
    window.addEventListener("popstate", (event) => {
        const openModals = document.querySelectorAll(".fixed.inset-0.z-50:not(.hidden)");
        if (openModals.length > 0) {
            openModals.forEach(modal => modal.classList.add("hidden"));
            history.pushState(null, "", location.href); // Prevent further back navigation
            return;
        }
        if (event.state?.tab) {
            switchTab(event.state.tab);
        }
    });

    try {
        if ("serviceWorker" in navigator) {
            navigator.serviceWorker.register("./sw.js").catch(e => console.warn("[SW]:", e.message));
        }
        if (localStorage.getItem("ARAF_BIOMETRIC_ENABLED") === "true") {
            const bioBtn = document.getElementById("btnBiometricToggle");
            if (bioBtn) {
                bioBtn.textContent = "ENABLED (ACTIVE)";
                bioBtn.className = "px-3 py-1.5 rounded-xl bg-[#00d09c] text-white font-black text-[10px]";
            }
        }
        initMobileSpeechUnlocker();
        renderAssets();
        updateVaultStatusBadges();
        syncLiveUsdInrRate();
        syncCoinDcxOrBinanceFeed();
        fetchMasterOrPublicIndianFeed();
        MASTER_ASSETS.forEach(a => loadRealHistoricalCandles(a.id));
        setTimeout(() => runFullMarketDiamondScan(false), 1800);
        setInterval(() => { if (!document.hidden) runFullMarketDiamondScan(false); }, 45000);
        setInterval(() => {
            const newsPane = document.getElementById("newsFeedList")?.closest("main");
            if (!document.hidden && auth.currentUser && newsPane && !newsPane.classList.contains("hidden")) {
                fetchRealFinancialNews();
            }
        }, 30000);

        if (marketPollIntervalId) clearInterval(marketPollIntervalId);
        // Connect to backend price WebSocket (replaces 5s polling)
        connectBackendPriceWs();
        // Keep Binance WS for depth/L2 data (100ms updates)
        connectBinanceWs();

        if (freshnessIntervalId) clearInterval(freshnessIntervalId);
        freshnessIntervalId = setInterval(checkFeedFreshnessWatchdog, 3000);

        if (sqOffIntervalId) clearInterval(sqOffIntervalId);
        sqOffIntervalId = setInterval(checkIntradaySquareOffReminder, 30000);
        checkIntradaySquareOffReminder();
    } catch (err) {
        console.error("[Boot Error]:", err);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootArafTerminal);
} else {
    bootArafTerminal();
}
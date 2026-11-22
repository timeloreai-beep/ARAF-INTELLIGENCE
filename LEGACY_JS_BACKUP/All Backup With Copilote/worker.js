/* ==========================================================================
   ARAF INTELLIGENCE — HARDENED ZERO-TRUST CLOUDFLARE WORKER (worker.js)
   Architecture: Zero-Trust Gateway • RS256 JWKS JWT Validation • DO Guard
                 Strict Mode & Quantity Gate • Position-Verified EXIT Guard
                 Unified Broker Relay Router (Custom Tunnel + Static IP)
                 Shoonya 1-Click Auto-Login • CoinDCX Floating P&L Sync
                 SL/Emergency Exit PositionBook Reconciler • OTP Rollback
                 Self-Healing Vault (safeReadVault) • Universal Stock Search
                 In-App Full News Reader • Dual Translator • IPO GMP Hub
                 Strict Share & Crypto AI Guardian (Gemini AQ. & AIza)
                 Standalone Admin Portal API • Secret Redaction
   ========================================================================== */

const FIREBASE_PROJECT_ID = "araf-intelligence";

const DEFAULT_ALLOWED_ORIGINS = [
    "https://timeloreai-beep.github.io",
    "https://araf-intelligence.netlify.app",
    "https://araf-admin-desk.netlify.app",
    "https://araf-intelligence.web.app",
    "https://araf-intelligence.firebaseapp.com"
];

const SERVER_CANONICAL_INSTRUMENTS = {
    NIFTY:      { broker: "SHOONYA", exchange: "NFO", segment: "FUT", lotSize: 65, maxQty: 1950, tickSize: 0.05, token: "26000" },
    BANKNIFTY:  { broker: "SHOONYA", exchange: "NFO", segment: "FUT", lotSize: 30, maxQty: 900,  tickSize: 0.05, token: "26009" },
    SENSEX:     { broker: "SHOONYA", exchange: "BFO", segment: "FUT", lotSize: 20, maxQty: 1000, tickSize: 0.05, token: "1" },
    BANKEX:     { broker: "SHOONYA", exchange: "BFO", segment: "FUT", lotSize: 15, maxQty: 900,  tickSize: 0.05, token: "12" },
    RELIANCE:   { broker: "SHOONYA", exchange: "NSE", segment: "EQ",  lotSize: 1,  maxQty: 5000, tickSize: 0.05, token: "2885" },
    HDFCBANK:   { broker: "SHOONYA", exchange: "NSE", segment: "EQ",  lotSize: 1,  maxQty: 5000, tickSize: 0.05, token: "1333" },
    TATAMOTORS: { broker: "SHOONYA", exchange: "NSE", segment: "EQ",  lotSize: 1,  maxQty: 5000, tickSize: 0.05, token: "3456" },
    TCS:        { broker: "SHOONYA", exchange: "BSE", segment: "EQ",  lotSize: 1,  maxQty: 3000, tickSize: 0.05, token: "532540" },
    INFY:       { broker: "SHOONYA", exchange: "BSE", segment: "EQ",  lotSize: 1,  maxQty: 3000, tickSize: 0.05, token: "500209" },
    BTCUSDT:    { broker: "COINDCX", marketPair: "B-BTC_USDT",  minQty: 0.0001, maxQty: 10,  maxLeverage: 10 },
    ETHUSDT:    { broker: "COINDCX", marketPair: "B-ETH_USDT",  minQty: 0.001,  maxQty: 100, maxLeverage: 10 },
    PAXGUSDT:   { broker: "COINDCX", marketPair: "B-PAXG_USDT", minQty: 0.001,  maxQty: 30,  maxLeverage: 10 }
};

class ApiError extends Error {
    constructor(message, status = 400, code = "API_ERROR") {
        super(message);
        this.status = status;
        this.code = code;
    }
}

function getCorrelationId() {
    const arr = new Uint8Array(4);
    crypto.getRandomValues(arr);
    return `REQ_${Date.now()}_${Array.from(arr).map(b => b.toString(16).padStart(2, "0")).join("")}`;
}

function redactSecrets(val) {
    if (val === null || val === undefined) return val;
    if (Array.isArray(val)) return val.map(redactSecrets);
    if (typeof val !== "object") return val;
    const sensitive = ["secret", "token", "key", "pin", "otp", "password", "pan", "aadhaar", "jkey", "authorization", "piiencrypted", "shoonyatoken", "coindcxsecret", "passphrase", "pintoken", "admintoken", "adminsecret", "apikey", "shoonyaapikey", "gemini_api_key"];
    const out = {};
    for (const [k, v] of Object.entries(val)) {
        const lk = k.toLowerCase();
        if (sensitive.some(s => lk.includes(s))) out[k] = "[REDACTED]";
        else out[k] = redactSecrets(v);
    }
    return out;
}

function auditLog(level, correlationId, event, data = {}) {
    console.log(JSON.stringify({
        ts: Date.now(),
        level,
        correlationId,
        event,
        data: redactSecrets(data)
    }));
}

function isOriginAllowed(origin, env) {
    if (!origin) return false;
    const envOrigins = (env.ALLOWED_ORIGINS || "")
        .split(",")
        .map(s => s.trim())
        .filter(s => s && s !== "*");
    if (envOrigins.includes(origin) || DEFAULT_ALLOWED_ORIGINS.includes(origin)) return true;
    if (env.ALLOW_LOCALHOST_DEV === "true") {
        try {
            const u = new URL(origin);
            if (u.protocol === "http:" && (u.hostname === "localhost" || u.hostname === "127.0.0.1")) {
                return true;
            }
        } catch (e) {}
    }
    return false;
}

function getCorsHeaders(request, env) {
    const reqOrigin = request.headers.get("Origin") || "";
    const allowOrigin = isOriginAllowed(reqOrigin, env) ? reqOrigin : "null";
    return {
        "Access-Control-Allow-Origin": allowOrigin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Order-Id, X-Admin-Token, X-Pin-Token",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin"
    };
}

function jsonResponse(data, status = 200, corsHeaders = {}, correlationId = "") {
    const headers = { "Content-Type": "application/json", ...corsHeaders };
    if (correlationId) headers["X-Correlation-Id"] = correlationId;
    return new Response(JSON.stringify(data), { status, headers });
}

function assertEnvironmentBindings(env) {
    const required = ["ARAF_KV", "RISK_GUARD", "VAULT_ENC_KEY"];
    for (const key of required) {
        if (!env[key]) throw new ApiError(`Fatal Configuration: Missing binding ${key}`, 503, "BINDING_MISSING");
    }
}

async function parseAndValidateJsonBody(request, maxBytes = 65536) {
    if (request.method !== "POST") return {};
    const contentType = request.headers.get("Content-Type") || "";
    if (!contentType.toLowerCase().includes("application/json")) {
        throw new ApiError("Content-Type must be application/json", 415, "UNSUPPORTED_MEDIA_TYPE");
    }
    const contentLength = request.headers.get("Content-Length");
    if (contentLength && parseInt(contentLength, 10) > maxBytes) {
        throw new ApiError("Payload exceeds 64KB limit", 413, "PAYLOAD_TOO_LARGE");
    }
    const reader = request.body ? request.body.getReader() : null;
    if (!reader) return {};
    let bytesRead = 0;
    const chunks = [];
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        bytesRead += value.length;
        if (bytesRead > maxBytes) throw new ApiError("Payload stream exceeded 64KB limit", 413, "PAYLOAD_TOO_LARGE");
        chunks.push(value);
    }
    const fullBuffer = new Uint8Array(bytesRead);
    let offset = 0;
    for (const chunk of chunks) {
        fullBuffer.set(chunk, offset);
        offset += chunk.length;
    }
    const rawText = new TextDecoder("utf-8").decode(fullBuffer);
    if (!rawText.trim()) return {};
    try {
        return JSON.parse(rawText);
    } catch (e) {
        throw new ApiError("Malformed JSON payload syntax", 400, "INVALID_JSON");
    }
}

function base64UrlToUint8Array(str) {
    const pad = "=".repeat((4 - (str.length % 4)) % 4);
    const base64 = (str + pad).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    const arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
}

let cachedJwks = { keys: [], expiresAt: 0 };
const MEMORY_RL_MAP = new Map();

function checkMemoryRateLimit(key, limit, windowMs = 60000) {
    const now = Date.now();
    const existing = MEMORY_RL_MAP.get(key);
    if (!existing || existing.resetAt <= now) {
        MEMORY_RL_MAP.set(key, { count: 1, resetAt: now + windowMs });
        return false;
    }
    existing.count += 1;
    if (existing.count > limit) return true;
    return false;
}

async function signStatelessToken(env, payload, ttlSeconds = 43200) {
    if (!env.VAULT_ENC_KEY) throw new Error("VAULT_ENC_KEY secret missing");
    const now = Math.floor(Date.now() / 1000);
    const body = { ...payload, iat: now, exp: now + ttlSeconds };
    const payloadJson = JSON.stringify(body);
    const payloadBytes = new TextEncoder().encode(payloadJson);
    const payloadPart = Array.from(payloadBytes).map(b => b.toString(16).padStart(2, "0")).join("");
    const signature = await generateHmacSha256(env.VAULT_ENC_KEY, payloadPart);
    return `${payloadPart}.${signature}`;
}

async function verifyStatelessToken(env, token) {
    if (!token || typeof token !== "string") return null;
    if (!env.VAULT_ENC_KEY) return null;
    const parts = token.split(".");
    if (parts.length !== 2) return null;
    const payloadBytes = new Uint8Array(parts[0].match(/.{2}/g)?.map(h => parseInt(h, 16)) || []);
    let payload;
    try {
        payload = JSON.parse(new TextDecoder().decode(payloadBytes));
    } catch (e) {
        return null;
    }
    const expected = await generateHmacSha256(env.VAULT_ENC_KEY, parts[0]);
    if (!timingSafeEqual(parts[1], expected)) return null;
    if (!payload.exp || payload.exp <= Math.floor(Date.now() / 1000)) return null;
    return payload;
}

async function getFirebasePublicKeys(forceRefresh = false) {
    if (!forceRefresh && Date.now() < cachedJwks.expiresAt && cachedJwks.keys.length > 0) {
        return cachedJwks.keys;
    }
    const res = await fetch("https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com");
    if (!res.ok) throw new Error("JWKS fetch failed");
    const cc = res.headers.get("Cache-Control") || "";
    const maxAgeMatch = cc.match(/max-age=(\d+)/);
    const ttlMs = maxAgeMatch ? parseInt(maxAgeMatch[1], 10) * 1000 : 3600 * 1000;
    const jwks = await res.json();
    cachedJwks = { keys: jwks.keys || [], expiresAt: Date.now() + ttlMs };
    return cachedJwks.keys;
}

async function verifyFirebaseToken(request, env) {
    const authHeader = request.headers.get("Authorization") || "";
    if (!authHeader.startsWith("Bearer ")) return null;
    const token = authHeader.substring(7).trim();
    const parts = token.split(".");
    if (parts.length !== 3) return null;

    try {
        const header = JSON.parse(new TextDecoder().decode(base64UrlToUint8Array(parts[0])));
        const payload = JSON.parse(new TextDecoder().decode(base64UrlToUint8Array(parts[1])));
        if (header.alg !== "RS256" || !header.kid) return null;

        const now = Math.floor(Date.now() / 1000);
        if (!payload.exp || payload.exp <= now) return null;
        if (!payload.iat || payload.iat > now + 300) return null;
        if (payload.auth_time && payload.auth_time > now + 300) return null;
        if (payload.aud !== FIREBASE_PROJECT_ID) return null;
        if (payload.iss !== `https://securetoken.google.com/${FIREBASE_PROJECT_ID}`) return null;
        if (!payload.sub || typeof payload.sub !== "string") return null;

        let keys = await getFirebasePublicKeys(false);
        let jwk = keys.find(k => k.kid === header.kid);
        if (!jwk) {
            keys = await getFirebasePublicKeys(true);
            jwk = keys.find(k => k.kid === header.kid);
        }
        if (!jwk) return null;

        const cryptoKey = await crypto.subtle.importKey(
            "jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]
        );
        const signedData = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
        const signature = base64UrlToUint8Array(parts[2]);
        const isValid = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", cryptoKey, signature, signedData);
        if (!isValid) return null;

        if (env && env.ARAF_KV) {
            const revokedBefore = await env.ARAF_KV.get(`USER_REVOKED:${payload.sub}`);
            if (revokedBefore && Number(payload.iat) < Number(revokedBefore)) {
                return null;
            }
        }

        return {
            uid: payload.sub,
            email: (payload.email || "").toLowerCase(),
            emailVerified: payload.email_verified === true,
            isAdmin: payload.admin === true
        };
    } catch (e) {
        return null;
    }
}

async function getVaultAesKey(env) {
    if (!env.VAULT_ENC_KEY) throw new Error("VAULT_ENC_KEY secret missing");
    const rawKey = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(env.VAULT_ENC_KEY));
    return crypto.subtle.importKey("raw", rawKey, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptVaultData(env, obj) {
    const key = await getVaultAesKey(env);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encoded = new TextEncoder().encode(JSON.stringify(obj));
    const cipherBuf = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, encoded);
    return JSON.stringify({
        iv: Array.from(iv).map(b => b.toString(16).padStart(2, "0")).join(""),
        ct: Array.from(new Uint8Array(cipherBuf)).map(b => b.toString(16).padStart(2, "0")).join("")
    });
}

async function decryptVaultData(env, storedJson) {
    const { iv, ct } = JSON.parse(storedJson);
    const key = await getVaultAesKey(env);
    const ivBytes = new Uint8Array(iv.match(/.{2}/g).map(h => parseInt(h, 16)));
    const ctBytes = new Uint8Array(ct.match(/.{2}/g).map(h => parseInt(h, 16)));
    const plainBuf = await crypto.subtle.decrypt({ name: "AES-GCM", iv: ivBytes }, key, ctBytes);
    return JSON.parse(new TextDecoder().decode(plainBuf));
}

// SELF-HEALING VAULT (SAFE READ - ZERO DECRYPTION FAILURES)
async function safeReadVault(env, uid) {
    if (!env.ARAF_KV || !uid) return {};
    const prevEnc = await env.ARAF_KV.get(`VAULT:${uid}`);
    if (!prevEnc) return {};
    try {
        return await decryptVaultData(env, prevEnc);
    } catch (e) {
        await env.ARAF_KV.delete(`VAULT:${uid}`).catch(() => {});
        return {};
    }
}

async function decryptKycProfile(env, kycObj) {
    if (!kycObj) return null;
    const out = { ...kycObj };
    if (out.piiEncrypted) {
        try {
            const pii = await decryptVaultData(env, out.piiEncrypted);
            Object.assign(out, pii);
        } catch (e) {
            out.piiDecryptError = true;
        }
        delete out.piiEncrypted;
    }
    return out;
}

async function derivePbkdf2PinHash(pin, saltHex) {
    const enc = new TextEncoder();
    const baseKey = await crypto.subtle.importKey("raw", enc.encode(pin), "PBKDF2", false, ["deriveBits"]);
    const saltBytes = new Uint8Array(saltHex.match(/.{2}/g).map(h => parseInt(h, 16)));
    const bits = await crypto.subtle.deriveBits(
        { name: "PBKDF2", salt: saltBytes, iterations: 100000, hash: "SHA-256" },
        baseKey, 256
    );
    return Array.from(new Uint8Array(bits)).map(b => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a, b) {
    if (typeof a !== "string" || typeof b !== "string") return false;
    const enc = new TextEncoder();
    const bufA = enc.encode(a);
    const bufB = enc.encode(b);
    if (bufA.length !== bufB.length) return false;
    let diff = 0;
    for (let i = 0; i < bufA.length; i++) diff |= bufA[i] ^ bufB[i];
    return diff === 0;
}

async function sha256Hash(text) {
    const msgBuffer = new TextEncoder().encode(text);
    const hashBuffer = await crypto.subtle.digest("SHA-256", msgBuffer);
    return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, "0")).join("");
}

async function generateHmacSha256(secret, payloadStr) {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(payloadStr));
    return Array.from(new Uint8Array(signature)).map(b => b.toString(16).padStart(2, "0")).join("");
}

function generateSecureToken() {
    const arr = new Uint8Array(24);
    crypto.getRandomValues(arr);
    return Array.from(arr).map(b => b.toString(16).padStart(2, "0")).join("");
}

function roundToTick(price, tickSize = 0.05) {
    const p = Number(price);
    const t = Number(tickSize) || 0.05;
    if (!Number.isFinite(p) || p <= 0) return 0;
    const decimals = (t.toString().split(".")[1] || "").length;
    return Number((Math.round(p / t) * t).toFixed(decimals));
}

function getIstDateStr() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

function parseShoonyaExpiryMs(exdStr) {
    const s = String(exdStr || "").trim().toUpperCase();
    if (!s) return NaN;
    const direct = Date.parse(s);
    if (Number.isFinite(direct)) return direct;
    const m = s.match(/^(\d{1,2})[-/\s]?([A-Z]{3})[-/\s]?(\d{4})$/);
    if (m) {
        const months = { JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5, JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11 };
        const monIdx = months[m[2]];
        if (monIdx !== undefined) {
            return Date.UTC(parseInt(m[3], 10), monIdx, parseInt(m[1], 10));
        }
    }
    return NaN;
}

function decodeHtmlEntities(str = "") {
    return String(str)
        .replace(/&amp;/gi, "&")
        .replace(/&lt;/gi, "<")
        .replace(/&gt;/gi, ">")
        .replace(/&quot;/gi, '"')
        .replace(/&#0*39;|&apos;/gi, "'")
        .replace(/&nbsp;/gi, " ")
        .replace(/&#(\d+);/g, (_, dec) => String.fromCharCode(Number(dec)))
        .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
}

function isPrivateOrUnsafeUrl(rawUrl) {
    try {
        const u = new URL(rawUrl);
        if (u.protocol !== "https:" && u.protocol !== "http:") return true;
        const h = u.hostname.toLowerCase();
        if (h === "localhost" || h === "127.0.0.1" || h === "::1" || h.endsWith(".local")) return true;
        if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(h)) return true;
        return false;
    } catch (e) {
        return true;
    }
}

async function translateTextArray(texts, tl, env = null) {
    if (!Array.isArray(texts) || texts.length === 0 || !tl || tl === "en") return texts;
    const cleanTexts = texts.slice(0, 15).map(raw => String(raw || "").slice(0, 450).trim());

    if (env?.GEMINI_API_KEY && cleanTexts.some(Boolean)) {
        try {
            const key = env.GEMINI_API_KEY;
            const prompt = `Translate these news strings to language code "${tl}". Return a JSON array with exactly ${cleanTexts.length} strings, preserving order:\n${JSON.stringify(cleanTexts)}`;
            let geminiUrl;
            let geminiBody;
            if (key.startsWith("AQ.")) {
                geminiUrl = "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash:generateContent";
                geminiBody = { contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: { thinkingBudget: 0, responseMimeType: "application/json" } };
            } else {
                geminiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${encodeURIComponent(key)}`;
                geminiBody = { contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: { thinkingBudget: 0, responseMimeType: "application/json" } };
            }
            const gr = await fetch(geminiUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json", ...(key.startsWith("AQ.") ? { "Authorization": `Bearer ${key}` } : {}) },
                body: JSON.stringify(geminiBody),
                cf: { cacheTtl: 300 }
            });
            if (gr.ok) {
                const gj = await gr.json().catch(() => null);
                const textOut = gj?.candidates?.[0]?.content?.parts?.map(p => p?.text || "").join("") || "";
                const parsed = JSON.parse(textOut);
                if (Array.isArray(parsed) && parsed.length === cleanTexts.length) {
                    return parsed.map((v, i) => String(v || "").trim() || cleanTexts[i]);
                }
            }
        } catch (e) {}
    }

    return Promise.all(cleanTexts.map(async (raw) => {
        const cleanText = String(raw || "").slice(0, 450).trim();
        if (!cleanText) return "";
        try {
            const lingvaUrl = `https://lingva.ml/api/v1/en/${encodeURIComponent(tl)}/${encodeURIComponent(cleanText)}`;
            const lr = await fetch(lingvaUrl, {
                headers: { "User-Agent": "Mozilla/5.0" },
                cf: { cacheTtl: 300 }
            });
            if (lr.ok) {
                const lj = await lr.json().catch(() => null);
                const translated = String(lj?.translation || "").trim();
                if (translated && translated.toLowerCase() !== cleanText.toLowerCase()) {
                    return translated;
                }
            }
        } catch (e) {}
        try {
            const gUrl = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=${encodeURIComponent(tl)}&dt=t&q=${encodeURIComponent(cleanText)}`;
            const gr = await fetch(gUrl, {
                headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" }
            });
            if (gr.ok) {
                const gj = await gr.json().catch(() => null);
                const translated = Array.isArray(gj?.[0]) ? gj[0].map(x => x?.[0] || "").join("") : "";
                if (translated && translated.trim() && translated.trim().toLowerCase() !== cleanText.toLowerCase()) {
                    return decodeHtmlEntities(translated.trim());
                }
            }
        } catch (e) {}
        return cleanText;
    }));
}

async function getUserRelayConfig(env, uid) {
    if (uid && env.ARAF_KV) {
        try {
            const profRaw = await env.ARAF_KV.get(`USER_PROFILE:${uid}`);
            if (profRaw) {
                const prof = JSON.parse(profRaw);
                const customUrl = String(prof?.preferences?.customRelayUrl || "").trim();
                if (customUrl.startsWith("https://") && !isPrivateOrUnsafeUrl(customUrl)) {
                    const vault = await safeReadVault(env, uid);
                    return { url: customUrl, isCustom: true, secret: String(vault.customRelaySecret || "") };
                }
            }
        } catch (e) {}
    }
    if (env.SHOONYA_RELAY_URL && String(env.SHOONYA_RELAY_URL).startsWith("https://")) {
        return {
            url: String(env.SHOONYA_RELAY_URL).trim(),
            isCustom: false,
            secret: String(env.BROKER_RELAY_SECRET || env.SHOONYA_RELAY_SECRET || "")
        };
    }
    return null;
}

async function brokerFetch(env, uid, targetUrl, options = {}) {
    const relay = await getUserRelayConfig(env, uid);
    if (relay) {
        if (!relay.secret || relay.secret.length < 16) {
            throw new ApiError("Relay Secret missing or too short (Fail-Closed). Configure BROKER_RELAY_SECRET.", 503, "RELAY_SECRET_MISSING");
        }
        return fetch(relay.url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Relay-Secret": relay.secret,
                "X-Relay-Timestamp": String(Date.now()),
                "X-Relay-Nonce": generateSecureToken()
            },
            body: JSON.stringify({
                targetUrl,
                method: options.method || "POST",
                headers: options.headers || {},
                body: options.body || ""
            })
        });
    }
    return fetch(targetUrl, options);
}

async function requireApprovedUser(env, authUser) {
    if (!authUser || !env.ARAF_KV) return { ok: false, status: 401, message: "Unauthorized" };
    const kycRaw = await env.ARAF_KV.get(`KYC:${authUser.uid}`);
    if (!kycRaw) return { ok: false, status: 403, message: "KYC submission required" };
    const kyc = JSON.parse(kycRaw);
    const st = kyc.kycStatus;
    if (st !== "APPROVED" && st !== "VERIFIED_COMPLIANT") {
        return { ok: false, status: 403, message: `KYC status is ${st}. Admin approval required.` };
    }
    return { ok: true, kyc };
}

async function requirePinSession(request, env, uid) {
    const pinToken = request.headers.get("X-Pin-Token");
    if (!pinToken) return false;
    const payload = await verifyStatelessToken(env, pinToken);
    return !!payload && payload.type === "PIN" && payload.uid === uid;
}

async function validateAdminAccess(request, env, authUser = null) {
    if (authUser && authUser.isAdmin === true) {
        return { ok: true, actor: authUser.email || authUser.uid };
    }
    const token = request.headers.get("X-Admin-Token");
    if (!token) return { ok: false };
    const sess = await verifyStatelessToken(env, token);
    if (!sess || sess.type !== "ADMIN") return { ok: false };
    return { ok: true, actor: sess.email || sess.actor || "Founder Desk" };
}

async function resolveVerifiedShoonyaContract(env, uid, vault, upperSym, spec, todayIst) {
    const cacheKey = `CONTRACT_MASTER:${upperSym}`;
    try {
        const edgeCached = await caches.default.match(
            new Request(`https://edge-cache.araf/shoonya-contract/${encodeURIComponent(cacheKey)}`)
        );
        if (edgeCached) {
            const cached = await edgeCached.json();
            if (cached?.verifiedDate === todayIst && cached?.tsym && cached?.token) return cached;
        }
    } catch (e) {}
    const cachedRaw = await env.ARAF_KV.get(cacheKey);
    if (cachedRaw) {
        const c = JSON.parse(cachedRaw);
        if (c.verifiedDate === todayIst && c.tsym && c.token) return c;
    }
    if (!vault?.shoonyaUid || !vault?.shoonyaToken) return null;

    try {
        const sr = await brokerFetch(env, uid, "https://api.shoonya.com/NorenWClientTP/SearchScrip", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, stext: upperSym, exch: spec.exchange }))}&jKey=${vault.shoonyaToken}`
        });
        const sd = await sr.json().catch(() => null);
        if (sd?.stat !== "Ok" || !Array.isArray(sd.values) || sd.values.length === 0) return null;

        let chosen = null;
        if (spec.segment === "EQ") {
            chosen = sd.values.find(v => String(v.token) === String(spec.token) || String(v.tsym).toUpperCase() === `${upperSym}-EQ` || String(v.tsym).toUpperCase() === upperSym);
        } else {
            const allFuts = sd.values.filter(v =>
                String(v.instname || "").startsWith("FUT") &&
                String(v.symname || "").toUpperCase() === upperSym &&
                v.exd
            );
            const exactLotFuts = allFuts.filter(v => Number(v.ls) === spec.lotSize);
            const futs = exactLotFuts.length > 0 ? exactLotFuts : allFuts;
            futs.sort((a, b) => (parseShoonyaExpiryMs(a.exd) || 0) - (parseShoonyaExpiryMs(b.exd) || 0));
            const nowMs = Date.now() - 86400000;
            chosen = futs.find(v => {
                const ms = parseShoonyaExpiryMs(v.exd);
                return Number.isFinite(ms) && ms >= nowMs;
            }) || futs[0] || null;
        }

        if (!chosen || !chosen.tsym) return null;
        const verified = {
            tsym: chosen.tsym,
            token: String(chosen.token),
            exch: chosen.exch || spec.exchange,
            lotSize: Number(chosen.ls || spec.lotSize),
            tickSize: Number(chosen.ti || spec.tickSize),
            expiry: chosen.exd || null,
            verifiedDate: todayIst
        };
        await caches.default.put(
            new Request(`https://edge-cache.araf/shoonya-contract/${encodeURIComponent(cacheKey)}`),
            new Response(JSON.stringify(verified), {
                headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=43200, max-age=43200" }
            })
        ).catch(() => {});
        return verified;
    } catch (e) {
        return null;
    }
}

async function verifyShoonyaOrderStatus(env, uid, vault, norenordno, maxChecks = 3) {
    for (let i = 0; i < maxChecks; i++) {
        if (i > 0) await new Promise(r => setTimeout(r, 650));
        const sRes = await brokerFetch(env, uid, "https://api.shoonya.com/NorenWClientTP/OrderBook", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
        }).catch(() => null);
        if (!sRes) continue;
        const orders = await sRes.json().catch(() => []);
        const found = Array.isArray(orders) ? orders.find(o => o.norenordno === norenordno) : null;
        if (found) {
            const st = String(found.status || "").toUpperCase();
            let norm = "UNKNOWN";
            if (st === "COMPLETE") norm = "FILLED";
            else if (st === "OPEN" || st === "PENDING" || st === "TRIGGER_PENDING") norm = "OPEN";
            else if (st === "REJECTED") norm = "REJECTED";
            else if (st === "CANCELED" || st === "CANCELLED") norm = "CANCELLED";
            const filledQty = Number(found.fillshares || 0);
            const avgPrice = Number(found.avgprc || found.prc || 0) || null;
            if (norm === "FILLED" || norm === "REJECTED" || norm === "CANCELLED" || i === maxChecks - 1) {
                return { status: norm, filledQty, avgPrice, rejectReason: found.rejreason || "" };
            }
        }
    }
    return { status: "UNKNOWN", filledQty: 0, avgPrice: null, rejectReason: "OrderBook confirmation timeout" };
}

async function getShoonyaPositionNetQty(env, uid, vault, exch, tsym, token) {
    const r = await brokerFetch(env, uid, "https://api.shoonya.com/NorenWClientTP/PositionBook", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, actid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
    }).catch(() => null);
    if (!r) return null;
    const list = await r.json().catch(() => null);
    if (!Array.isArray(list)) return null;
    let net = 0;
    list.forEach(p => {
        if (p.exch === exch && (p.tsym === tsym || String(p.token) === String(token))) {
            net += Number(p.netqty || 0);
        }
    });
    return net;
}

async function getCoinDcxOpenPositionsWithMark(env, uid, vault) {
    const payloadStr = JSON.stringify({ timestamp: Date.now(), page: "1", size: "50", margin_currency_short_name: ["USDT", "INR"] });
    const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);
    const [posRes, rtRes] = await Promise.all([
        brokerFetch(env, uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/positions", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": sig },
            body: payloadStr
        }),
        fetch("https://public.coindcx.com/market_data/v3/current_prices/futures/rt").catch(() => null)
    ]);
    const list = await posRes.json().catch(() => null);
    if (!Array.isArray(list)) return null;
    const rtJson = rtRes ? await rtRes.json().catch(() => null) : null;
    const prices = rtJson?.prices || rtJson || {};

    return list.filter(p => Number(p.active_pos ?? p.active_units ?? p.total_quantity ?? 0) !== 0).map(p => {
        const rawPos = p.active_pos !== undefined ? Number(p.active_pos) : Number(p.active_units || p.total_quantity || 0);
        const signedQty = p.active_pos !== undefined ? rawPos : (String(p.side || "").toLowerCase() === "short" ? -Math.abs(rawPos) : Math.abs(rawPos));
        const avgPrice = Number(p.avg_price || p.entry_price || 0);
        const rpnl = Number(p.realized_pnl || 0);
        let upnl = null;
        if (Number.isFinite(Number(p.unrealized_pnl))) {
            upnl = Number(p.unrealized_pnl);
        } else {
            const mpObj = prices[p.pair] || prices[p.market] || {};
            const markPrice = Number(mpObj.mp || mpObj.ls || p.mark_price || 0);
            if (markPrice > 0 && avgPrice > 0) {
                upnl = (markPrice - avgPrice) * signedQty;
            }
        }
        return {
            raw: p,
            market: p.pair || p.market || "",
            signedQty,
            avgPrice,
            rpnl,
            upnl,
            quoteCurrency: p.margin_currency_short_name || "USDT",
            incomplete: upnl === null
        };
    });
}

async function computeServerBrokerPnlInr(env, uid) {
    if (!env.ARAF_KV || !uid) return null;
    const vault = await safeReadVault(env, uid);

    let totalInr = 0;
    let openPositionsCount = 0;
    let fetchedAny = false;
    let incompletePnl = false;

    await Promise.allSettled([
        (async () => {
            if (!vault.shoonyaUid || !vault.shoonyaToken) return;
            const r = await brokerFetch(env, uid, "https://api.shoonya.com/NorenWClientTP/PositionBook", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, actid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
            });
            const list = await r.json().catch(() => null);
            if (Array.isArray(list)) {
                fetchedAny = true;
                list.forEach(p => {
                    if (Number(p.netqty || 0) !== 0) openPositionsCount++;
                    totalInr += Number(p.rpnl || 0) + Number(p.urmtom || 0);
                });
            }
        })(),
        (async () => {
            if (!vault.coindcxKey || !vault.coindcxSecret) return;
            const [positions, fxRes] = await Promise.all([
                getCoinDcxOpenPositionsWithMark(env, uid, vault),
                fetch("https://api.exchangerate-api.com/v4/latest/USD").catch(() => null)
            ]);
            const fxJson = fxRes ? await fxRes.json().catch(() => null) : null;
            const usdInr = Number(fxJson?.rates?.INR || 0);
            if (Array.isArray(positions)) {
                fetchedAny = true;
                positions.forEach(p => {
                    openPositionsCount++;
                    if (p.incomplete) {
                        incompletePnl = true;
                        return;
                    }
                    const leg = p.rpnl + p.upnl;
                    if (p.quoteCurrency === "INR") totalInr += leg;
                    else if (usdInr > 0) totalInr += leg * usdInr;
                    else incompletePnl = true;
                });
            }
        })()
    ]);

    return fetchedAny ? { totalInr, openPositionsCount, incompletePnl } : null;
}

export class OrderRiskGuardDO {
    constructor(state, env) {
        this.ctx = state;
        this.env = env;
        this.storage = state.storage;
    }

    getIstDate() {
        return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
    }

    defaultRisk(todayIst) {
        return {
            tradingDay: todayIst,
            dailyPnlInr: 0,
            tradesCount: 0,
            isHardLocked: false,
            lockedAt: null,
            lockReason: null,
            lockedConfigDate: null,
            capitalInr: 100000,
            maxDailyLossPct: 2.5,
            dailyLossLimitInr: -2500,
            maxTradesPerDay: 3
        };
    }

    defaultAutoTrade(todayIst) {
        return {
            enabled: false,
            dailyBudgetInr: 10000,
            usedBudgetInr: 0,
            reservedBudgetInr: 0,
            tradingDay: todayIst,
            maxOpenAutoPositions: 3,
            activeAutoPositions: 0,
            minScoreThreshold: 90,
            allowedMarkets: "ALL",
            updatedAt: Date.now(),
            auditTrail: []
        };
    }

    async alarm() {
        const uid = await this.storage.get("ownerUid");
        const todayIst = this.getIstDate();
        if (!uid) return;

        const snap = await computeServerBrokerPnlInr(this.env, uid);
        let risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
        if (risk.tradingDay !== todayIst) {
            risk = { ...risk, tradingDay: todayIst, dailyPnlInr: 0, tradesCount: 0, isHardLocked: false, lockedAt: null, lockReason: null };
        }

        if (snap) {
            risk.dailyPnlInr = snap.totalInr;
            if (risk.dailyPnlInr <= risk.dailyLossLimitInr) {
                risk.isHardLocked = true;
                risk.lockedAt = risk.lockedAt || Date.now();
                risk.lockReason = risk.lockReason || "DAILY_LOSS_LIMIT_BREACHED";
            }
            await this.storage.put("riskState", risk);
            if (snap.openPositionsCount > 0 && !risk.isHardLocked) {
                await this.storage.setAlarm(Date.now() + 10000);
            }
        }
    }

    async fetch(request) {
        const url = new URL(request.url);
        const action = url.pathname.replace("/", "");
        const body = await request.json().catch(() => ({}));
        const todayIst = this.getIstDate();

        if (action === "pin-check-and-record") {
            const { matched } = body;
            const lock = (await this.storage.get("pinLock")) || { attempts: 0, lockedUntil: 0 };
            if (Date.now() < lock.lockedUntil) {
                const sec = Math.ceil((lock.lockedUntil - Date.now()) / 1000);
                return new Response(JSON.stringify({ allowed: false, message: `Too many attempts! Locked for ${sec}s.` }), { status: 429 });
            }
            if (matched === true) {
                await this.storage.delete("pinLock");
                return new Response(JSON.stringify({ allowed: true }), { status: 200 });
            }
            if (matched === false) {
                lock.attempts += 1;
                if (lock.attempts >= 5) lock.lockedUntil = Date.now() + 15 * 60 * 1000;
                await this.storage.put("pinLock", lock);
                return new Response(JSON.stringify({ allowed: false, message: `Wrong PIN! Remaining attempts: ${Math.max(0, 5 - lock.attempts)}` }), { status: 401 });
            }
            return new Response(JSON.stringify({ allowed: true }), { status: 200 });
        }

        if (action === "reserve-uid") {
            const { prefix, authUid } = body;
            for (let i = 0; i < 8; i++) {
                const rand = new Uint32Array(1);
                crypto.getRandomValues(rand);
                const candidate = `${prefix}-${((rand[0] % 90000000) + 10000000)}`;
                const taken = await this.storage.get(`uid:${candidate}`);
                if (!taken) {
                    await this.storage.put(`uid:${candidate}`, authUid);
                    return new Response(JSON.stringify({ ok: true, traderUid: candidate }), { status: 200 });
                }
            }
            return new Response(JSON.stringify({ ok: false }), { status: 503 });
        }

        if (action === "set-risk-config") {
            const { ownerUid, capitalInr, maxDailyLossPct, maxTradesPerDay, isAdminOverride = false } = body;
            if (ownerUid) await this.storage.put("ownerUid", ownerUid);
            let risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
            if (risk.tradingDay !== todayIst) {
                risk = { ...risk, tradingDay: todayIst, dailyPnlInr: 0, tradesCount: 0, isHardLocked: false, lockedAt: null, lockReason: null, lockedConfigDate: null };
            }
            if (!isAdminOverride && (risk.tradesCount > 0 || risk.isHardLocked)) {
                return new Response(JSON.stringify({
                    success: false,
                    message: "Anti-Revenge Lock: Risk limit locked after first trade. Changes apply tomorrow."
                }), { status: 403 });
            }
            const cap = Math.min(100000000, Math.max(5000, Number(capitalInr) || 100000));
            const pct = Math.min(10, Math.max(0.5, Number(maxDailyLossPct) || 2));
            const maxTr = Math.min(20, Math.max(1, parseInt(maxTradesPerDay, 10) || 3));
            risk.capitalInr = cap;
            risk.maxDailyLossPct = pct;
            risk.dailyLossLimitInr = -Math.round((cap * pct) / 100);
            risk.maxTradesPerDay = maxTr;
            if (!isAdminOverride) {
                risk.lockedConfigDate = todayIst;
            }
            await this.storage.put("riskState", risk);
            return new Response(JSON.stringify({ success: true, risk }), { status: 200 });
        }

        if (action === "precheck-and-lock") {
            const { ownerUid, clientOrderId, operationType, symbol, side, qty, mode, isAutoTrade = false } = body;
            if (ownerUid) await this.storage.put("ownerUid", ownerUid);
            const existingOrder = await this.storage.get(`idem:${clientOrderId}`);
            if (existingOrder) {
                return new Response(JSON.stringify({
                    allowed: false,
                    duplicate: true,
                    reason: "Duplicate order prevented by atomic lock.",
                    existingOrder
                }), { status: 409 });
            }

            let risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
            if (risk.tradingDay !== todayIst) {
                risk = { ...risk, tradingDay: todayIst, dailyPnlInr: 0, tradesCount: 0, isHardLocked: false, lockedAt: null, lockReason: null, lockedConfigDate: null };
            }

            if (operationType === "ENTRY") {
                if (risk.isHardLocked || risk.dailyPnlInr <= risk.dailyLossLimitInr) {
                    risk.isHardLocked = true;
                    risk.lockedAt = risk.lockedAt || Date.now();
                    await this.storage.put("riskState", risk);
                    return new Response(JSON.stringify({
                        allowed: false,
                        reason: `SERVER_HARD_LOCK: ${risk.lockReason || "Daily loss limit reached"}. New entries blocked.`
                    }), { status: 403 });
                }
                if (!isAutoTrade && risk.tradesCount >= risk.maxTradesPerDay) {
                    return new Response(JSON.stringify({
                        allowed: false,
                        reason: `OVERTRADING_BLOCKED: Max ${risk.maxTradesPerDay} entries allowed per day.`
                    }), { status: 403 });
                }
                if (!isAutoTrade) risk.tradesCount += 1;
                await this.storage.put("riskState", risk);
            }

            if (mode === "PAPER" && operationType === "EXIT") {
                const pPos = (await this.storage.get(`paperPos:${symbol}`)) || 0;
                if (pPos === 0) {
                    return new Response(JSON.stringify({
                        allowed: false,
                        reason: `NO_OPEN_POSITION: No open paper position for ${symbol}`
                    }), { status: 400 });
                }
                if ((pPos > 0 && side !== "SELL") || (pPos < 0 && side !== "BUY")) {
                    return new Response(JSON.stringify({
                        allowed: false,
                        reason: "WRONG_EXIT_SIDE: Exit side mismatches open paper position"
                    }), { status: 400 });
                }
                if (Number(qty) > Math.abs(pPos)) {
                    return new Response(JSON.stringify({
                        allowed: false,
                        reason: `EXIT_QTY_EXCEEDS_POSITION: Max closeable paper qty is ${Math.abs(pPos)}`
                    }), { status: 400 });
                }
            }

            const orderRecord = { clientOrderId, operationType, symbol, side, qty, mode, state: "SUBMITTED", createdAt: Date.now() };
            await this.storage.put(`idem:${clientOrderId}`, orderRecord);

            return new Response(JSON.stringify({ allowed: true, risk, orderRecord }), { status: 200 });
        }

        if (action === "update-order-state") {
            const { clientOrderId, state, brokerOrderId, avgPrice, filledQty, scheduleAlarm = false, paperUpdate } = body;
            const prev = (await this.storage.get(`idem:${clientOrderId}`)) || {};
            const updated = { ...prev, state, brokerOrderId, avgPrice, filledQty, updatedAt: Date.now() };
            await this.storage.put(`idem:${clientOrderId}`, updated);

            if (prev.operationType === "EXIT" && (state === "FILLED" || state === "PAPER_FILLED" || state === "EXIT_VERIFIED_NET_ZERO")) {
                const at = await this.storage.get("autoTradeState");
                if (at && at.activeAutoPositions > 0) {
                    at.activeAutoPositions = Math.max(0, at.activeAutoPositions - 1);
                    await this.storage.put("autoTradeState", at);
                }
            }

            if (paperUpdate && paperUpdate.symbol) {
                const cur = (await this.storage.get(`paperPos:${paperUpdate.symbol}`)) || 0;
                const delta = paperUpdate.side === "BUY" ? Number(paperUpdate.qty) : -Number(paperUpdate.qty);
                const newPos = Number((cur + delta).toFixed(8));
                await this.storage.put(`paperPos:${paperUpdate.symbol}`, Math.abs(newPos) < 1e-8 ? 0 : newPos);
            }

            if (scheduleAlarm) await this.storage.setAlarm(Date.now() + 10000);
            return new Response(JSON.stringify({ success: true, order: updated }), { status: 200 });
        }

        if (action === "rollback-lock") {
            const { clientOrderId, operationType, state = "FAILED", reason = "" } = body;
            const prev = (await this.storage.get(`idem:${clientOrderId}`)) || {};
            const updated = { ...prev, state, reason, updatedAt: Date.now() };
            await this.storage.put(`idem:${clientOrderId}`, updated);

            if (operationType === "ENTRY" && prev.state === "SUBMITTED") {
                const risk = await this.storage.get("riskState");
                if (risk && risk.tradesCount > 0) {
                    risk.tradesCount -= 1;
                    await this.storage.put("riskState", risk);
                }
            }
            return new Response(JSON.stringify({ success: true, order: updated }), { status: 200 });
        }

        if (action === "critical-unprotected-lock") {
            const { reason = "CRITICAL_UNPROTECTED_POSITION" } = body;
            let risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
            risk.isHardLocked = true;
            risk.lockedAt = Date.now();
            risk.lockReason = reason;
            await this.storage.put("riskState", risk);
            return new Response(JSON.stringify({ success: true, risk }), { status: 200 });
        }

        if (action === "sync-pnl") {
            const { ownerUid, dailyPnlInr } = body;
            if (ownerUid) await this.storage.put("ownerUid", ownerUid);
            let risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
            if (risk.tradingDay !== todayIst) {
                risk = { ...risk, tradingDay: todayIst, dailyPnlInr: 0, tradesCount: 0, isHardLocked: false, lockedAt: null, lockReason: null };
            }
            if (Number.isFinite(Number(dailyPnlInr))) {
                risk.dailyPnlInr = Number(dailyPnlInr);
                if (risk.dailyPnlInr <= risk.dailyLossLimitInr) {
                    risk.isHardLocked = true;
                    risk.lockedAt = risk.lockedAt || Date.now();
                    risk.lockReason = risk.lockReason || "DAILY_LOSS_LIMIT_BREACHED";
                }
            }
            await this.storage.put("riskState", risk);
            return new Response(JSON.stringify({ success: true, risk }), { status: 200 });
        }

        if (action === "get-risk-state") {
            let risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
            if (risk.tradingDay !== todayIst) {
                risk = { ...risk, tradingDay: todayIst, dailyPnlInr: 0, tradesCount: 0, isHardLocked: false, lockedAt: null, lockReason: null };
                await this.storage.put("riskState", risk);
            }
            return new Response(JSON.stringify({ success: true, risk }), { status: 200 });
        }

        if (action === "admin-reset-lock") {
            const risk = (await this.storage.get("riskState")) || this.defaultRisk(todayIst);
            risk.isHardLocked = false;
            risk.lockedAt = null;
            risk.lockReason = null;
            risk.dailyPnlInr = 0;
            await this.storage.put("riskState", risk);
            return new Response(JSON.stringify({ success: true, risk }), { status: 200 });
        }

        if (action === "get-auto-trade-state") {
            let at = (await this.storage.get("autoTradeState")) || this.defaultAutoTrade(todayIst);
            if (at.tradingDay !== todayIst) {
                at = { ...at, tradingDay: todayIst, usedBudgetInr: 0, reservedBudgetInr: 0, activeAutoPositions: 0 };
                await this.storage.put("autoTradeState", at);
            }
            return new Response(JSON.stringify({ success: true, autoTrade: at }), { status: 200 });
        }

        if (action === "set-auto-trade-config") {
            const { enabled, dailyBudgetInr, allowedMarkets, actor = "USER" } = body;
            let at = (await this.storage.get("autoTradeState")) || this.defaultAutoTrade(todayIst);
            if (at.tradingDay !== todayIst) {
                at = { ...at, tradingDay: todayIst, usedBudgetInr: 0, reservedBudgetInr: 0 };
            }
            const prevState = { enabled: at.enabled, dailyBudgetInr: at.dailyBudgetInr, allowedMarkets: at.allowedMarkets };
            if (typeof enabled === "boolean") at.enabled = enabled;
            if (dailyBudgetInr !== undefined) {
                const b = Math.min(10000000, Math.max(1000, Number(dailyBudgetInr) || 10000));
                at.dailyBudgetInr = b;
            }
            if (["ALL", "INDIAN", "CRYPTO"].includes(allowedMarkets)) {
                at.allowedMarkets = allowedMarkets;
            }
            at.updatedAt = Date.now();
            at.auditTrail = [
                { ts: Date.now(), actor, prevState, nextState: { enabled: at.enabled, dailyBudgetInr: at.dailyBudgetInr, allowedMarkets: at.allowedMarkets } },
                ...(at.auditTrail || [])
            ].slice(0, 25);
            await this.storage.put("autoTradeState", at);
            return new Response(JSON.stringify({ success: true, autoTrade: at }), { status: 200 });
        }

        if (action === "reserve-auto-trade-budget") {
            const { clientOrderId, requiredInr, assetCategory = "indian" } = body;
            let at = (await this.storage.get("autoTradeState")) || this.defaultAutoTrade(todayIst);
            if (at.tradingDay !== todayIst) {
                at = { ...at, tradingDay: todayIst, usedBudgetInr: 0, reservedBudgetInr: 0 };
            }
            if (!at.enabled) {
                return new Response(JSON.stringify({ allowed: false, reason: "AUTO_TRADE_OFF: Switch is OFF." }), { status: 403 });
            }
            if (at.allowedMarkets === "INDIAN" && assetCategory !== "indian") {
                return new Response(JSON.stringify({ allowed: false, reason: "AUTO_TRADE_MARKET_DISABLED: Crypto auto-trade disabled." }), { status: 403 });
            }
            if (at.allowedMarkets === "CRYPTO" && assetCategory === "indian") {
                return new Response(JSON.stringify({ allowed: false, reason: "AUTO_TRADE_MARKET_DISABLED: Indian auto-trade disabled." }), { status: 403 });
            }
            const reqAmt = Number(requiredInr);
            if (!Number.isFinite(reqAmt) || reqAmt <= 0) {
                return new Response(JSON.stringify({ allowed: false, reason: "INVALID_BUDGET_RESERVATION" }), { status: 400 });
            }
            const remaining = at.dailyBudgetInr - (at.usedBudgetInr + at.reservedBudgetInr);
            if (reqAmt > remaining) {
                return new Response(JSON.stringify({
                    allowed: false,
                    reason: `DAILY_BUDGET_EXCEEDED: Required ₹${Math.round(reqAmt)} > Remaining ₹${Math.max(0, Math.round(remaining))}`
                }), { status: 403 });
            }
            at.reservedBudgetInr += reqAmt;
            await this.storage.put(`atRes:${clientOrderId}`, reqAmt);
            await this.storage.put("autoTradeState", at);
            return new Response(JSON.stringify({ allowed: true, autoTrade: at, remainingBudgetInr: at.dailyBudgetInr - (at.usedBudgetInr + at.reservedBudgetInr) }), { status: 200 });
        }

        if (action === "commit-auto-trade-budget") {
            const { clientOrderId } = body;
            const resAmt = Number((await this.storage.get(`atRes:${clientOrderId}`)) || 0);
            let at = (await this.storage.get("autoTradeState")) || this.defaultAutoTrade(todayIst);
            if (resAmt > 0) {
                at.reservedBudgetInr = Math.max(0, (at.reservedBudgetInr || 0) - resAmt);
                at.usedBudgetInr = (at.usedBudgetInr || 0) + resAmt;
                at.activeAutoPositions = (at.activeAutoPositions || 0) + 1;
                await this.storage.delete(`atRes:${clientOrderId}`);
                await this.storage.put("autoTradeState", at);
            }
            return new Response(JSON.stringify({ success: true, autoTrade: at }), { status: 200 });
        }

        if (action === "release-auto-trade-budget") {
            const { clientOrderId } = body;
            const resAmt = Number((await this.storage.get(`atRes:${clientOrderId}`)) || 0);
            let at = (await this.storage.get("autoTradeState")) || this.defaultAutoTrade(todayIst);
            if (resAmt > 0) {
                at.reservedBudgetInr = Math.max(0, (at.reservedBudgetInr || 0) - resAmt);
                await this.storage.delete(`atRes:${clientOrderId}`);
                await this.storage.put("autoTradeState", at);
            }
            return new Response(JSON.stringify({ success: true, autoTrade: at }), { status: 200 });
        }

        return new Response("Not Found", { status: 404 });
    }
}

export default {
    async fetch(request, env) {
        const cors = getCorsHeaders(request, env);
        const correlationId = getCorrelationId();

        if (request.method === "OPTIONS") {
            return new Response(null, { status: 204, headers: cors });
        }

        if (request.method !== "GET" && request.method !== "POST") {
            return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED", error: "Method not allowed" }, 405, cors, correlationId);
        }

        const url = new URL(request.url);
        const target = url.searchParams.get("target");

        try {
            assertEnvironmentBindings(env);
            const body = await parseAndValidateJsonBody(request, 65536);

            if (target === "health") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                let brokerStatus = "NOT_CONNECTED";
                let shoonyaStatus = "NOT_CONNECTED";
                let coindcxStatus = "NOT_CONNECTED";

                const authHeader = request.headers.get("Authorization") || "";
                if (authHeader.startsWith("Bearer ")) {
                    const authUser = await verifyFirebaseToken(request, env);
                    if (authUser) {
                        const vault = await safeReadVault(env, authUser.uid);
                        if (vault.shoonyaUid && vault.shoonyaToken) shoonyaStatus = "SESSION_LINKED";
                        if (vault.coindcxKey && vault.coindcxSecret) coindcxStatus = "SESSION_LINKED";
                        if (shoonyaStatus === "SESSION_LINKED" || coindcxStatus === "SESSION_LINKED") {
                            brokerStatus = "SESSION_LINKED";
                        }
                    }
                }

                return jsonResponse({
                    ok: true,
                    service: "araf-gateway",
                    liveTradingEnabled: env.LIVE_TRADING_ENABLED === "true",
                    brokerStatus,
                    brokers: { shoonya: shoonyaStatus, coindcx: coindcxStatus },
                    timestamp: Date.now()
                }, 200, cors, correlationId);
            }

            if (target === "send_real_otp") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`OTP_RL:${ip}`, 10, 60000)) return jsonResponse({ success: false, code: "RATE_LIMITED", message: "Too many OTP requests" }, 429, cors, correlationId);

                const email = String(body.to || "").trim().toLowerCase();
                const purpose = body.purpose === "PIN_RESET" ? "PIN_RESET" : "SIGNUP";
                if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
                    return jsonResponse({ success: false, code: "INVALID_EMAIL", message: "Invalid email" }, 400, cors, correlationId);
                }

                const cdKey = `OTP_CD:${email}:${purpose}`;
                if (await env.ARAF_KV.get(cdKey)) {
                    return jsonResponse({ success: false, code: "OTP_COOLDOWN", message: "Please wait 60s before requesting another OTP" }, 429, cors, correlationId);
                }

                if (!env.GMAIL_BRIDGE_URL || !env.GMAIL_BRIDGE_SECRET) {
                    return jsonResponse({ success: false, fallbackFirebaseRecommended: true, message: "OTP bridge unconfigured" }, 503, cors, correlationId);
                }

                const rand = new Uint32Array(1);
                crypto.getRandomValues(rand);
                const otp = String((rand[0] % 900000) + 100000);
                const hash = await sha256Hash(`${email}:${purpose}:${otp}`);

                await Promise.all([
                    env.ARAF_KV.put(`OTP:${email}:${purpose}`, JSON.stringify({ hash, attempts: 0 }), { expirationTtl: 600 }),
                    env.ARAF_KV.put(cdKey, "1", { expirationTtl: 60 })
                ]);

                const bRes = await fetch(env.GMAIL_BRIDGE_URL, {
                    method: "POST",
                    redirect: "follow",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ secret: env.GMAIL_BRIDGE_SECRET, to: email, otp })
                }).catch(err => ({ ok: false, status: 502, error: err.message }));

                const rawResp = bRes && typeof bRes.text === "function" ? await bRes.text().catch(() => "") : "";
                let bData = {};
                try { bData = JSON.parse(rawResp); } catch (e) {}

                if (!bRes.ok || bData.success !== true) {
                    await Promise.allSettled([
                        env.ARAF_KV.delete(`OTP:${email}:${purpose}`),
                        env.ARAF_KV.delete(cdKey)
                    ]);
                    const reason = bData.error || (rawResp.includes("accounts.google.com") ? "Apps Script access must be Anyone" : "Bridge dispatch failed. Rolled back cleanly.");
                    auditLog("WARN", correlationId, "OTP_BRIDGE_FAIL_ROLLBACK", { email, purpose, reason });
                    return jsonResponse({ success: false, fallbackFirebaseRecommended: true, message: reason }, 502, cors, correlationId);
                }

                auditLog("INFO", correlationId, "OTP_SENT", { email, purpose });
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            if (target === "verify_server_otp") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const email = String(body.email || "").trim().toLowerCase();
                const code = String(body.code || "").trim();
                const purpose = body.purpose === "PIN_RESET" ? "PIN_RESET" : "SIGNUP";
                const kvKey = `OTP:${email}:${purpose}`;
                const raw = await env.ARAF_KV.get(kvKey);
                if (!raw) return jsonResponse({ success: false, code: "OTP_EXPIRED", message: "OTP expired or not found" }, 400, cors, correlationId);
                const rec = JSON.parse(raw);
                if (rec.attempts >= 5) {
                    await env.ARAF_KV.delete(kvKey);
                    return jsonResponse({ success: false, code: "OTP_LOCKED", message: "Too many failed OTP attempts" }, 429, cors, correlationId);
                }

                const checkHash = await sha256Hash(`${email}:${purpose}:${code}`);
                if (!timingSafeEqual(rec.hash, checkHash)) {
                    rec.attempts += 1;
                    await env.ARAF_KV.put(kvKey, JSON.stringify(rec), { expirationTtl: 600 });
                    return jsonResponse({ success: false, code: "OTP_INVALID", message: "Invalid OTP code" }, 401, cors, correlationId);
                }

                await env.ARAF_KV.delete(kvKey);
                await env.ARAF_KV.put(`OTP_VERIFIED:${email}:${purpose}`, "1", { expirationTtl: 1800 });
                auditLog("INFO", correlationId, "OTP_VERIFIED", { email, purpose });
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            if (target === "login_with_trader_uid") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`LOGIN_RL:${ip}`, 15, 60000)) return jsonResponse({ success: false, code: "RATE_LIMITED", message: "Too many login attempts" }, 429, cors, correlationId);

                const uidKey = `UID_MAP:${String(body.traderUid || "").trim().toUpperCase()}`;
                const mappedRaw = await env.ARAF_KV.get(uidKey);
                if (!mappedRaw || !env.FIREBASE_WEB_API_KEY) {
                    return jsonResponse({ success: false, code: "INVALID_CREDENTIALS", message: "Invalid Trader UID or Password!" }, 401, cors, correlationId);
                }
                const mapped = JSON.parse(mappedRaw);

                const fbRes = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${env.FIREBASE_WEB_API_KEY}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: mapped.email, password: String(body.password || ""), returnSecureToken: false })
                });
                if (!fbRes.ok) return jsonResponse({ success: false, code: "INVALID_CREDENTIALS", message: "Invalid Trader UID or Password!" }, 401, cors, correlationId);
                return jsonResponse({ success: true, email: mapped.email }, 200, cors, correlationId);
            }

            if (target === "submit_kyc") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (!authUser) return jsonResponse({ success: false, code: "UNAUTHORIZED", message: "Unauthorized" }, 401, cors, correlationId);

                const existingKyc = await env.ARAF_KV.get(`KYC:${authUser.uid}`);
                if (existingKyc) {
                    const ek = JSON.parse(existingKyc);
                    if (ek.kycStatus === "APPROVED" || ek.kycStatus === "VERIFIED_COMPLIANT" || ek.kycStatus === "PENDING_ADMIN_REVIEW") {
                        return jsonResponse({ success: true, traderUid: ek.traderUid, kycStatus: ek.kycStatus }, 200, cors, correlationId);
                    }
                }

                const otpKey = `OTP_VERIFIED:${authUser.email}:SIGNUP`;
                const otpVerified = await env.ARAF_KV.get(otpKey);
                if (!authUser.emailVerified && !otpVerified) {
                    return jsonResponse({ success: false, code: "EMAIL_NOT_VERIFIED", message: "Email verification required before KYC" }, 403, cors, correlationId);
                }

                const fullName = String(body.fullName || "").trim().toUpperCase();
                const pan = String(body.pan || "").trim().toUpperCase();
                const aadhaarLast4 = String(body.aadhaarLast4 || "").trim();
                if (fullName.length < 3 || !/^[A-Z]{5}[0-9]{4}[A-Z]{1}$/.test(pan) || !/^\d{4}$/.test(aadhaarLast4)) {
                    return jsonResponse({ success: false, code: "INVALID_KYC_FIELDS", message: "Invalid KYC fields" }, 400, cors, correlationId);
                }

                const seg = ["ALL", "FNO_ONLY", "CRYPTO_ONLY"].includes(body.authorizedSegment) ? body.authorizedSegment : "ALL";
                const prefix = fullName.replace(/[^A-Z]/g, "").slice(0, 4).padEnd(4, "X");
                const regStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName("GLOBAL_UID_REGISTRY"));
                const uidRes = await regStub.fetch("https://guard/reserve-uid", {
                    method: "POST",
                    body: JSON.stringify({ prefix, authUid: authUser.uid })
                });
                const uidData = await uidRes.json();
                if (!uidRes.ok || !uidData.ok) return jsonResponse({ success: false, code: "UID_ALLOC_FAILED", message: "Could not allocate UID" }, 503, cors, correlationId);
                const traderUid = uidData.traderUid;

                const panMasked = `${pan.slice(0, 2)}XXXXX${pan.slice(7)}`;
                const piiEncrypted = await encryptVaultData(env, {
                    pan,
                    aadhaarLast4,
                    phone: String(body.phone || ""),
                    dob: String(body.dob || ""),
                    incomeBracket: String(body.incomeBracket || "")
                });
                const trialExp = new Date(Date.now() + 30 * 86400 * 1000).toISOString();
                const kycDoc = {
                    ownerUid: authUser.uid,
                    traderUid,
                    fullName,
                    panMasked,
                    aadhaarMasked: `•••• ${aadhaarLast4}`,
                    incomeBracket: String(body.incomeBracket || "-"),
                    experience: String(body.experience || "-"),
                    authorizedSegment: seg,
                    kycStatus: "PENDING_ADMIN_REVIEW",
                    piiEncrypted,
                    submittedAt: Date.now()
                };
                const profile = {
                    fullName,
                    traderUid,
                    email: authUser.email,
                    panMasked,
                    kycStatus: "PENDING_ADMIN_REVIEW",
                    isVipFree: false,
                    subscriptionExpiresAt: trialExp
                };

                await Promise.all([
                    env.ARAF_KV.put(`KYC:${authUser.uid}`, JSON.stringify(kycDoc)),
                    env.ARAF_KV.put(`UID_MAP:${traderUid}`, JSON.stringify({ uid: authUser.uid, email: authUser.email })),
                    env.ARAF_KV.put(`USER_PROFILE:${authUser.uid}`, JSON.stringify({ profile, preferences: {} })),
                    otpVerified ? env.ARAF_KV.delete(otpKey) : Promise.resolve()
                ]);

                auditLog("INFO", correlationId, "KYC_SUBMITTED", { uid: authUser.uid, traderUid });
                return jsonResponse({ success: true, traderUid }, 200, cors, correlationId);
            }

            if (target === "set_master_pin") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (!authUser) return jsonResponse({ success: false, code: "UNAUTHORIZED", message: "Unauthorized" }, 401, cors, correlationId);

                const existingPin = await env.ARAF_KV.get(`PIN:${authUser.uid}`);
                const resetKey = `OTP_VERIFIED:${authUser.email}:PIN_RESET`;
                const resetOtpOk = await env.ARAF_KV.get(resetKey);

                if (existingPin) {
                    const hasPinSess = await requirePinSession(request, env, authUser.uid);
                    if (!hasPinSess && !resetOtpOk) {
                        return jsonResponse({ success: false, code: "PIN_RESET_AUTH_REQUIRED", message: "Existing PIN session or PIN_RESET OTP required to change PIN" }, 403, cors, correlationId);
                    }
                }

                const pin = String(body.pin || "").trim();
                if (!/^\d{6}$/.test(pin)) return jsonResponse({ success: false, code: "INVALID_PIN_FORMAT", message: "PIN must be 6 digits" }, 400, cors, correlationId);

                const saltArr = new Uint8Array(16);
                crypto.getRandomValues(saltArr);
                const saltHex = Array.from(saltArr).map(b => b.toString(16).padStart(2, "0")).join("");
                const pinHash = await derivePbkdf2PinHash(pin, saltHex);

                await Promise.all([
                    env.ARAF_KV.put(`PIN:${authUser.uid}`, JSON.stringify({ saltHex, pinHash, updatedAt: Date.now() })),
                    resetOtpOk ? env.ARAF_KV.delete(resetKey) : Promise.resolve()
                ]);

                const pinToken = await signStatelessToken(env, { type: "PIN", uid: authUser.uid }, 43200);

                auditLog("INFO", correlationId, "PIN_SET", { uid: authUser.uid });
                return jsonResponse({ success: true, pinToken }, 200, cors, correlationId);
            }

            if (target === "verify_pin") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (!authUser) return jsonResponse({ success: false, code: "UNAUTHORIZED", message: "Unauthorized" }, 401, cors, correlationId);

                const pinRaw = await env.ARAF_KV.get(`PIN:${authUser.uid}`);
                if (!pinRaw) return jsonResponse({ success: false, code: "PIN_NOT_SET", message: "PIN not set" }, 404, cors, correlationId);

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                const preRes = await doStub.fetch("https://guard/pin-check-and-record", { method: "POST", body: JSON.stringify({}) });
                if (!preRes.ok) return jsonResponse(await preRes.json(), preRes.status, cors, correlationId);

                const stored = JSON.parse(pinRaw);
                const derived = await derivePbkdf2PinHash(String(body.pin || "").trim(), stored.saltHex);
                const matched = timingSafeEqual(derived, stored.pinHash);

                const recRes = await doStub.fetch("https://guard/pin-check-and-record", { method: "POST", body: JSON.stringify({ matched }) });
                if (!matched) {
                    auditLog("WARN", correlationId, "PIN_VERIFY_FAIL", { uid: authUser.uid });
                    return jsonResponse(await recRes.json(), recRes.status, cors, correlationId);
                }

                const pinToken = await signStatelessToken(env, { type: "PIN", uid: authUser.uid }, 43200);

                return jsonResponse({ success: true, pinToken }, 200, cors, correlationId);
            }

            if (target === "get_master_user") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (!authUser) return jsonResponse({ error: "Unauthorized" }, 401, cors, correlationId);

                const [profRaw, kycRaw, pinRaw] = await Promise.all([
                    env.ARAF_KV.get(`USER_PROFILE:${authUser.uid}`),
                    env.ARAF_KV.get(`KYC:${authUser.uid}`),
                    env.ARAF_KV.get(`PIN:${authUser.uid}`)
                ]);
                if (!profRaw && !kycRaw) return jsonResponse({ success: false }, 404, cors, correlationId);

                const userData = profRaw ? JSON.parse(profRaw) : { profile: {}, preferences: {} };
                if (kycRaw) {
                    const kyc = JSON.parse(kycRaw);
                    userData.profile = {
                        ...(userData.profile || {}),
                        fullName: kyc.fullName,
                        traderUid: kyc.traderUid,
                        panMasked: kyc.panMasked,
                        kycStatus: kyc.kycStatus
                    };
                }
                return jsonResponse({ success: true, hasPin: !!pinRaw, fullData: { ...userData, tradeHistory: [] } }, 200, cors, correlationId);
            }

            if (target === "save_master_user") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (!authUser) return jsonResponse({ error: "Unauthorized" }, 401, cors, correlationId);

                const profRaw = await env.ARAF_KV.get(`USER_PROFILE:${authUser.uid}`);
                const current = profRaw ? JSON.parse(profRaw) : { profile: {}, preferences: {} };
                const rawPrefs = body.fullData?.preferences || {};
                const cleanPrefs = {};
                ["voiceLang", "newsLang", "aiChatLang"].forEach(k => {
                    if (typeof rawPrefs[k] === "string" && rawPrefs[k].length <= 16) cleanPrefs[k] = rawPrefs[k];
                });
                current.preferences = { ...(current.preferences || {}), ...cleanPrefs };
                await env.ARAF_KV.put(`USER_PROFILE:${authUser.uid}`, JSON.stringify(current));
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            if (target === "save_relay_url") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (!authUser) return jsonResponse({ error: "Unauthorized" }, 401, cors, correlationId);

                const customRelayUrl = String(body.customRelayUrl || "").trim();
                const customRelaySecret = String(body.customRelaySecret || "").trim();

                if (customRelayUrl !== "") {
                    let parsedUrl;
                    try {
                        parsedUrl = new URL(customRelayUrl);
                    } catch (e) {
                        return jsonResponse({ success: false, error: "Invalid URL format" }, 400, cors, correlationId);
                    }
                    if (parsedUrl.protocol !== "https:" || isPrivateOrUnsafeUrl(customRelayUrl)) {
                        return jsonResponse({ success: false, error: "Relay URL must be a public https:// address" }, 400, cors, correlationId);
                    }
                }

                const vault = await safeReadVault(env, authUser.uid);
                if (customRelayUrl === "") {
                    delete vault.customRelaySecret;
                } else if (customRelaySecret) {
                    if (customRelaySecret.length < 16) {
                        return jsonResponse({ success: false, error: "Relay Secret must be at least 16 characters" }, 400, cors, correlationId);
                    }
                    vault.customRelaySecret = customRelaySecret;
                } else if (!vault.customRelaySecret) {
                    return jsonResponse({ success: false, error: "Custom Relay Secret (min 16 chars) required for your PC tunnel" }, 400, cors, correlationId);
                }

                await env.ARAF_KV.put(`VAULT:${authUser.uid}`, await encryptVaultData(env, vault));

                const profRaw = await env.ARAF_KV.get(`USER_PROFILE:${authUser.uid}`);
                const userObj = profRaw ? JSON.parse(profRaw) : { profile: {}, preferences: {} };
                userObj.preferences = userObj.preferences || {};
                userObj.preferences.customRelayUrl = customRelayUrl;
                await env.ARAF_KV.put(`USER_PROFILE:${authUser.uid}`, JSON.stringify(userObj));
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            if (target === "logout_session") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                if (authUser) {
                    await env.ARAF_KV.put(`USER_REVOKED:${authUser.uid}`, String(Math.floor(Date.now() / 1000)), { expirationTtl: 86400 });
                    auditLog("INFO", correlationId, "LOGOUT", { uid: authUser.uid });
                }
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            // BROKER VAULT APIS WITH SAFE READ (ZERO DECRYPTION FAILURES)
            if (target === "get_broker_vault_status") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);
                const shoonyaLinked = !!(vault.shoonyaUid && vault.shoonyaToken);
                const coindcxLinked = !!(vault.coindcxKey && vault.coindcxSecret);
                return jsonResponse({
                    success: true,
                    shoonyaLinked,
                    coindcxLinked,
    
                    shoonyaStatus: shoonyaLinked ? "SESSION_SAVED" : "NOT_CONNECTED",
                    coindcxStatus: coindcxLinked ? "SESSION_SAVED" : "NOT_CONNECTED"
                }, 200, cors, correlationId);
            }

            if (target === "save_broker_vault") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);

                if (body.broker === "SHOONYA") {
                    const suid = String(body.shoonyaUid || "").trim().toUpperCase();
                    const stok = String(body.shoonyaToken || "").trim();
                    if (!suid || !stok) return jsonResponse({ error: "Shoonya UID and token required" }, 400, cors, correlationId);
                    vault.shoonyaUid = suid;
                    vault.shoonyaToken = stok;
                } else if (body.broker === "COINDCX") {
                    const ckey = String(body.coindcxKey || "").trim();
                    const csec = String(body.coindcxSecret || "").trim();
                    if (!ckey || !csec) return jsonResponse({ error: "CoinDCX key and secret required" }, 400, cors, correlationId);
                    vault.coindcxKey = ckey;
                    vault.coindcxSecret = csec;
                } else {
                    return jsonResponse({ error: "Unsupported broker" }, 400, cors, correlationId);
                }

                const enc = await encryptVaultData(env, vault);
                await env.ARAF_KV.put(`VAULT:${authUser.uid}`, enc);
                auditLog("INFO", correlationId, "VAULT_SAVED", { uid: authUser.uid, broker: body.broker });
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            if (target === "shoonya_auto_login") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);

                const uid = String(body.uid || vault.shoonyaUid || "").trim().toUpperCase();
                const password = String(body.password || "").trim();
                const factor2 = String(body.factor2 || "").trim();
                const apiKey = String(body.apiKey || vault.shoonyaApiKey || "").trim();
                const vendorCode = String(body.vendorCode || vault.shoonyaVendor || `${uid}_U`).trim().toUpperCase();

                if (!uid || !password || !factor2 || !apiKey) {
                    return jsonResponse({
                        success: false,
                        error: "User ID, Password, TOTP/PAN, and API Key are required (First setup requires API Key)."
                    }, 400, cors, correlationId);
                }

                const pwdHash = await sha256Hash(password);
                const appKeyHash = await sha256Hash(`${uid}|${apiKey}`);

                const jData = {
                    apkversion: "1.0.0",
                    uid,
                    pwd: pwdHash,
                    factor2,
                    vc: vendorCode,
                    appkey: appKeyHash,
                    imei: "araf_terminal_pro",
                    source: "API"
                };

                let d = {};
                let brokerFetchError = "";
                try {
                    const sRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/QuickAuth", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: `jData=${encodeURIComponent(JSON.stringify(jData))}`
                    });
                    const responseText = await sRes.text();
                    try {
                        d = JSON.parse(responseText);
                    } catch (e) {
                        const cleanText = responseText.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
                        d = {
                            stat: "Not_Ok",
                            emsg: cleanText ? cleanText.slice(0, 160) : "Invalid Shoonya response",
                            error: cleanText ? cleanText.slice(0, 160) : "Invalid Shoonya response"
                        };
                    }
                } catch (err) {
                    brokerFetchError = err?.message || "Relay connection failed";
                }

                if (brokerFetchError) {
                    return jsonResponse({ success: false, error: "PC Relay Tunnel Offline/Expired", errorReason: brokerFetchError }, 503, cors, correlationId);
                }

                if (d?.stat === "Ok" && d?.susertoken) {
                    vault.shoonyaUid = uid;
                    vault.shoonyaToken = d.susertoken;
                    vault.shoonyaApiKey = apiKey;
                    vault.shoonyaVendor = vendorCode;

                    await env.ARAF_KV.put(`VAULT:${authUser.uid}`, await encryptVaultData(env, vault));
                    auditLog("INFO", correlationId, "SHOONYA_AUTO_LOGIN_SUCCESS", { uid: authUser.uid, shoonyaUid: uid });
                    return jsonResponse({ success: true, uname: d.uname || uid }, 200, cors, correlationId);
                }

                const loginError = d?.emsg || d?.error || d?.d?.emsg || d?.d?.error || "Shoonya login rejected. Verify Password, TOTP, API Key, or Static IP Relay.";
                return jsonResponse({ success: false, error: loginError, errorReason: loginError }, 401, cors, correlationId);
            }

            if (target === "delete_broker_vault") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);

                if (body.broker === "SHOONYA") {
                    delete vault.shoonyaUid;
                    delete vault.shoonyaToken;
                    delete vault.shoonyaApiKey;
                    delete vault.shoonyaVendor;
                } else if (body.broker === "COINDCX") {
                    delete vault.coindcxKey;
                    delete vault.coindcxSecret;
                } else {
                    await env.ARAF_KV.delete(`VAULT:${authUser.uid}`);
                    return jsonResponse({ success: true }, 200, cors, correlationId);
                }

                if (Object.keys(vault).length === 0) {
                    await env.ARAF_KV.delete(`VAULT:${authUser.uid}`);
                } else {
                    await env.ARAF_KV.put(`VAULT:${authUser.uid}`, await encryptVaultData(env, vault));
                }
                auditLog("INFO", correlationId, "VAULT_UNLINKED", { uid: authUser.uid, broker: body.broker });
                return jsonResponse({ success: true }, 200, cors, correlationId);
            }

            if (target === "public_market_feed") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`PUB_MKT_RL:${ip}`, 120, 60000)) return jsonResponse({ success: false, code: "RATE_LIMITED" }, 429, cors, correlationId);

                const publicCache = caches.default;
                const publicCacheKey = new Request("https://edge-cache.araf/public_market_feed");
                const cachedPublic = await publicCache.match(publicCacheKey);
                if (cachedPublic) {
                    return new Response(cachedPublic.body, {
                        status: cachedPublic.status,
                        headers: { "Content-Type": "application/json", ...cors }
                    });
                }

                const browserHeaders = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    "Accept": "application/json,text/html"
                };

                const fetchYahooSingle = async (ySym) => {
                    for (const host of ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]) {
                        try {
                            const r = await fetch(`https://${host}/v8/finance/chart/${encodeURIComponent(ySym)}?interval=5m&range=1d`, {
                                headers: browserHeaders,
                                cf: { cacheTtl: 4 }
                            });
                            if (!r.ok) continue;
                            const j = await r.json().catch(() => null);
                            const meta = j?.chart?.result?.[0]?.meta;
                            const q = j?.chart?.result?.[0]?.indicators?.quote?.[0];
                            const price = Number(meta?.regularMarketPrice || 0);
                            const prev = Number(meta?.chartPreviousClose || meta?.previousClose || price);
                            if (price > 0) {
                                const highs = (q?.high || []).filter(v => Number.isFinite(v));
                                const lows = (q?.low || []).filter(v => Number.isFinite(v));
                                const pct = prev > 0 ? ((price - prev) / prev) * 100 : 0;
                                return {
                                    price,
                                    high: highs.length ? Math.max(...highs) : price,
                                    low: lows.length ? Math.min(...lows) : price,
                                    change: `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`,
                                    event_time: (meta?.regularMarketTime || Math.floor(Date.now() / 1000)) * 1000,
                                    quality: "DELAYED",
                                    source: "YAHOO_FINANCE_PROXY"
                                };
                            }
                        } catch (e) {}
                    }
                    return null;
                };

                const fetchGoogleFinanceBackup = async (gSym) => {
                    if (!gSym) return null;
                    try {
                        const r = await fetch(`https://www.google.com/finance/quote/${encodeURIComponent(gSym)}`, { headers: browserHeaders });
                        if (!r.ok) return null;
                        const html = await r.text();
                        const priceM = html.match(/data-last-price="([0-9.]+)"/);
                        const prevM = html.match(/data-previous-close="([0-9.]+)"/);
                        const price = Number(priceM?.[1] || 0);
                        const prev = Number(prevM?.[1] || price);
                        if (price > 0) {
                            const pct = prev > 0 ? ((price - prev) / prev) * 100 : 0;
                            return {
                                price, high: price, low: price,
                                change: `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`,
                                event_time: Date.now(),
                                quality: "DELAYED",
                                source: "GOOGLE_FINANCE_BACKUP"
                            };
                        }
                    } catch (e) {}
                    return null;
                };

                const map = {
                    nifty:      { y: ["^NSEI"], g: "NIFTY_50:INDEXNSE" },
                    banknifty:  { y: ["^NSEBANK"], g: "NIFTY_BANK:INDEXNSE" },
                    sensex:     { y: ["^BSESN"], g: "SENSEX:INDEXBOM" },
                    bankex:     { y: ["BSE-BANK.BO"], g: "BANKEX:INDEXBOM" },
                    vix:        { y: ["^INDIAVIX"], g: "INDIA_VIX:INDEXNSE" },
                    reliance:   { y: ["RELIANCE.NS"], g: "RELIANCE:NSE" },
                    hdfcbank:   { y: ["HDFCBANK.NS"], g: "HDFCBANK:NSE" },
                    tatamotors: { y: ["TATAMOTORS.NS", "TMPV.NS"], g: "TATAMOTORS:NSE" },
                    tcs_bse:    { y: ["TCS.BO", "TCS.NS"], g: "TCS:BOM" },
                    infy_bse:   { y: ["INFY.BO", "INFY.NS"], g: "INFY:BOM" }
                };

                const out = {};
                await Promise.allSettled(Object.entries(map).map(async ([k, cfg]) => {
                    for (const ySym of cfg.y) {
                        const hit = await fetchYahooSingle(ySym);
                        if (hit) { out[k] = hit; return; }
                    }
                    const gHit = await fetchGoogleFinanceBackup(cfg.g);
                    if (gHit) out[k] = gHit;
                }));
                const publicResponse = jsonResponse({ success: Object.keys(out).length > 0, ...out }, 200, cors, correlationId);
                const publicCacheResponse = publicResponse.clone();
                await publicCache.put(publicCacheKey, new Response(publicCacheResponse.clone().body, {
                    headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=4, max-age=4" }
                })).catch(() => {});
                return publicResponse;
            }

            if (target === "master_market_feed") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                
const vault = await safeReadVault(env, authUser.uid);
                if (!vault.shoonyaUid || !vault.shoonyaToken) {
                    return jsonResponse({ success: false, shoonyaConnected: false, reason: "SHOONYA_NOT_CONNECTED" }, 200, cors, correlationId);
                }
                const relayOwnerUid = authUser.uid;
                const specs = [
                    { k: "nifty50", exch: "NSE", token: "26000" },
                    { k: "banknifty", exch: "NSE", token: "26009" },
                    { k: "vix", exch: "NSE", token: "26017" },
                    { k: "sensex", exch: "BSE", token: "1" },
                    { k: "bankex", exch: "BSE", token: "12" },
                    { k: "reliance", exch: "NSE", token: "2885" },
                    { k: "hdfcbank", exch: "NSE", token: "1333" },
                    { k: "tatamotors", exch: "NSE", token: "3456" },
                    { k: "tcs_bse", exch: "BSE", token: "532540" },
                    { k: "infy_bse", exch: "BSE", token: "500209" }
                ];
                const quotes = {};
                await Promise.allSettled(specs.map(async (s) => {
                    const sr = await brokerFetch(env, relayOwnerUid, "https://api.shoonya.com/NorenWClientTP/GetQuotes", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, exch: s.exch, token: s.token }))}&jKey=${vault.shoonyaToken}`
                    });
                    const d = await sr.json().catch(() => null);
                    const lp = Number(d?.lp || 0);
                    if (d?.stat === "Ok" && lp > 0) {
                        const c = Number(d.c || lp);
                        const pct = c > 0 ? ((lp - c) / c) * 100 : 0;
                        quotes[s.k] = {
                            price: lp,
                            high: Number(d.h || lp),
                            low: Number(d.l || lp),
                            change: `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`,
                            tbq: Number(d.tbq || 0),
                            tsq: Number(d.tsq || 0),
                            event_time: Date.now(),
                            quality: "LIVE",
                            source: "SHOONYA_MASTER"
                        };
                    }
                }));
                return jsonResponse({ success: Object.keys(quotes).length > 0, quotes }, 200, cors, correlationId);
            }

            if (target === "search_stock") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const q = String(url.searchParams.get("q") || "").trim();
                const qUp = q.toUpperCase();
                const exchFilter = String(url.searchParams.get("exchange") || "ALL").toUpperCase();
                const typeFilter = String(url.searchParams.get("type") || "ALL").toUpperCase();
                const page = Math.max(1, parseInt(url.searchParams.get("page") || "1", 10) || 1);
                const pageSize = Math.min(50, Math.max(5, parseInt(url.searchParams.get("pageSize") || "20", 10) || 20));

                if (q.length < 1 && exchFilter === "ALL" && typeFilter === "ALL") {
                    return jsonResponse({ success: true, totalMatches: 0, page: 1, pageSize, totalPages: 1, pageRange: "0-0 of 0", items: [] }, 200, cors, correlationId);
                }

                const catalogue = [];
                const seenIds = new Set();

                // 1. Canonical Indices & F&O Contracts
                for (const [sym, spec] of Object.entries(SERVER_CANONICAL_INSTRUMENTS)) {
                    const ex = spec.exchange || spec.broker;
                    const uid = `${ex}:${sym}`;
                    if (!seenIds.has(uid)) {
                        seenIds.add(uid);
                        catalogue.push({
                            id: `cat_${ex}_${sym}`.toLowerCase(),
                            orderKey: sym,
                            symbol: uid,
                            yahooSymbol: sym === "NIFTY" ? "^NSEI" : (sym === "BANKNIFTY" ? "^NSEBANK" : (sym === "SENSEX" ? "^BSESN" : (spec.broker === "COINDCX" ? `${sym.replace(/USDT$/, "")}-USD` : `${sym}.${ex === "BSE" ? "BO" : "NS"}`))),
                            name: `${sym} (${spec.segment || spec.broker})`,
                            exchange: ex,
                            instrumentType: spec.segment === "FUT" ? "FUTURES" : (spec.broker === "COINDCX" ? "CRYPTO" : "EQUITY"),
                            category: spec.broker === "COINDCX" ? "crypto" : "indian",
                            price: null,
                            change: "--",
                            changePct: 0,
                            dataQuality: "CATALOGUE"
                        });
                    }
                }

                // 2. Full Cached NSE & BSE Equity Universe
                try {
                    const indiaCacheKey = new Request("https://edge-cache.araf/universe_scan_india");
                    const indiaCached = await caches.default.match(indiaCacheKey);
                    if (indiaCached) {
                        const indCache = await indiaCached.json();
                        for (const it of (indCache.items || [])) {
                            if (!seenIds.has(it.symbol)) {
                                seenIds.add(it.symbol);
                                catalogue.push(it);
                            }
                        }
                    }
                } catch (e) {}

                // 3. Full Crypto Spot/Perp Catalogue (Cached 60s)
                try {
                    let cryptoList = null;
                    const cryptoCacheKey = new Request("https://edge-cache.araf/crypto_catalogue");
                    const cryptoCache = caches.default;
                    const cryptoCached = await cryptoCache.match(cryptoCacheKey);
                    if (cryptoCached) cryptoList = await cryptoCached.json().then(j => j.items).catch(() => null);
                    if (!cryptoList) {
                        const bRes = await fetch("https://api.binance.com/api/v3/ticker/24hr");
                        if (bRes.ok) {
                            const bArr = await bRes.json().catch(() => []);
                            cryptoList = (Array.isArray(bArr) ? bArr : [])
                                .filter(t => t.symbol && t.symbol.endsWith("USDT") && Number(t.lastPrice || 0) > 0)
                                .map(t => {
                                    const s = t.symbol.toUpperCase();
                                    const lp = Number(t.lastPrice);
                                    const chg = Number(t.priceChangePercent || 0);
                                    return {
                                        id: `cat_bin_${s}`.toLowerCase(),
                                        orderKey: s,
                                        symbol: `BINANCE:${s}`,
                                        yahooSymbol: `${s.replace(/USDT$/, "")}-USD`,
                                        name: `${s.replace(/USDT$/, "")} / USDT`,
                                        exchange: "BINANCE",
                                        instrumentType: "CRYPTO",
                                        category: "crypto",
                                        price: lp,
                                        high: Number(t.highPrice || lp),
                                        low: Number(t.lowPrice || lp),
                                        changePct: Number(chg.toFixed(2)),
                                        change: `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`,
                                        volume: Number(t.volume || 0),
                                        turnoverInr: Number(t.quoteVolume || 0),
                                        dataQuality: "LIVE"
                                    };
                                });
                            await cryptoCache.put(cryptoCacheKey, new Response(JSON.stringify({ asOf: Date.now(), items: cryptoList }), {
                                headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=60, max-age=60" }
                            })).catch(() => {});
                        }
                    }
                    for (const cIt of (cryptoList || [])) {
                        if (!seenIds.has(cIt.symbol)) {
                            seenIds.add(cIt.symbol);
                            catalogue.push(cIt);
                        }
                    }
                } catch (e) {}

                // 4. Filter & Exact-Match Priority Sorting
                let matched = catalogue.filter(it => {
                    if (exchFilter !== "ALL" && it.exchange !== exchFilter) return false;
                    if (typeFilter !== "ALL" && String(it.instrumentType || "").toUpperCase() !== typeFilter) return false;
                    if (!qUp) return true;
                    return (
                        String(it.orderKey || "").toUpperCase().includes(qUp) ||
                        String(it.symbol || "").toUpperCase().includes(qUp) ||
                        String(it.name || "").toUpperCase().includes(qUp) ||
                        String(it.isin || "").toUpperCase().includes(qUp)
                    );
                });

                matched.sort((a, b) => {
                    const aExact = a.orderKey === qUp || a.symbol === qUp ? 2 : (String(a.orderKey || "").startsWith(qUp) ? 1 : 0);
                    const bExact = b.orderKey === qUp || b.symbol === qUp ? 2 : (String(b.orderKey || "").startsWith(qUp) ? 1 : 0);
                    if (aExact !== bExact) return bExact - aExact;
                    return (b.turnoverInr || 0) - (a.turnoverInr || 0);
                });

                // 5. Fallback Yahoo Global/ISIN Search if local catalogue has few matches
                let providerLimited = false;
                if (q.length >= 2 && matched.length < 5 && (exchFilter === "ALL" || exchFilter === "NSE" || exchFilter === "BSE")) {
                    try {
                        const sRes = await fetch(`https://query1.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(q)}&quotesCount=15&newsCount=0`, {
                            headers: { "User-Agent": "Mozilla/5.0" },
                            cf: { cacheTtl: 60 }
                        });
                        const sJson = await sRes.json().catch(() => ({}));
                        const quotes = (sJson?.quotes || []).filter(x => x.symbol && (x.quoteType === "EQUITY" || x.quoteType === "INDEX" || x.quoteType === "CRYPTOCURRENCY" || x.quoteType === "ETF"));
                        for (const qt of quotes) {
                            const ex = qt.symbol.endsWith(".BO") ? "BSE" : (qt.symbol.endsWith(".NS") ? "NSE" : (qt.exchDisp || "GLOBAL"));
                            const ordK = qt.symbol.replace(/\.(NS|BO)$/i, "").toUpperCase();
                            const fullSym = `${ex}:${ordK}`;
                            if (seenIds.has(fullSym)) continue;
                            if (exchFilter !== "ALL" && ex !== exchFilter) continue;
                            seenIds.add(fullSym);
                            matched.push({
                                id: `yf_${ex}_${ordK}`.toLowerCase(),
                                yahooSymbol: qt.symbol,
                                orderKey: ordK,
                                symbol: fullSym,
                                name: qt.shortname || qt.longname || qt.symbol,
                                exchange: ex,
                                instrumentType: qt.quoteType || "EQUITY",
                                category: (qt.quoteType === "CRYPTOCURRENCY" || qt.symbol.includes("-USD")) ? "crypto" : "indian",
                                price: null,
                                change: "--",
                                changePct: 0,
                                dataQuality: "DELAYED"
                            });
                        }
                        providerLimited = true;
                    } catch (e) {}
                }

                const totalMatches = matched.length;
                const totalPages = Math.max(1, Math.ceil(totalMatches / pageSize));
                const safePage = Math.min(page, totalPages);
                const startIdx = (safePage - 1) * pageSize;
                const pageItems = matched.slice(startIdx, startIdx + pageSize);
                const rangeEnd = Math.min(totalMatches, startIdx + pageItems.length);

                return jsonResponse({
                    success: true,
                    totalMatches,
                    catalogueSize: catalogue.length,
                    page: safePage,
                    pageSize,
                    totalPages,
                    pageRange: totalMatches > 0 ? `${startIdx + 1}-${rangeEnd} of ${totalMatches}` : "0-0 of 0",
                    providerLimited,
                    sourceNote: "NSE + BSE + Crypto + Canonical F&O Catalogue (Distinct Exchange Identities)",
                    items: pageItems
                }, 200, cors, correlationId);
            }

            if (target === "asset_details") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);

                const symbol = String(url.searchParams.get("symbol") || "").trim().toUpperCase();
                const yahooSymbol = String(url.searchParams.get("yahooSymbol") || "").trim();
                const assetName = String(url.searchParams.get("name") || "").trim();
                const category = String(url.searchParams.get("category") || "").trim().toLowerCase();
                if (!symbol) return jsonResponse({ success: false, code: "SYMBOL_REQUIRED" }, 400, cors, correlationId);

                const cacheKey = new Request(`https://edge-cache.araf/asset_details/${encodeURIComponent(symbol)}`);
                const cached = await caches.default.match(cacheKey);
                if (cached) {
                    return new Response(cached.body, {
                        status: cached.status,
                        headers: { "Content-Type": "application/json", ...cors }
                    });
                }

                let chartSymbol = yahooSymbol;
                if (!chartSymbol) {
                    if (category === "crypto" || symbol.endsWith("USDT")) {
                        chartSymbol = `${symbol.replace(/USDT$/, "")}-USD`;
                    } else if (symbol.includes(":")) {
                        const [exchange, raw] = symbol.split(":");
                        chartSymbol = `${raw}.${exchange === "BSE" ? "BO" : "NS"}`;
                    } else {
                        chartSymbol = symbol;
                    }
                }

                let details = {
                    symbol,
                    yahooSymbol: chartSymbol,
                    open: null,
                    prevClose: null,
                    dayHigh: null,
                    dayLow: null,
                    week52High: null,
                    week52Low: null,
                    volume: null,
                    turnoverInr: null,
                    marketCap: null,
                    sector: null,
                    pe: null
                };

                try {
                    const chartRes = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(chartSymbol)}?range=1y&interval=1d`, {
                        headers: { "User-Agent": "Mozilla/5.0" },
                        cf: { cacheTtl: 60 }
                    });
                    if (chartRes.ok) {
                        const chart = await chartRes.json();
                        const result = chart?.chart?.result?.[0];
                        const meta = result?.meta || {};
                        const quote = result?.indicators?.quote?.[0] || {};
                        const closes = (quote.close || []).filter(v => Number.isFinite(v));
                        const highs = (quote.high || []).filter(v => Number.isFinite(v));
                        const lows = (quote.low || []).filter(v => Number.isFinite(v));
                        details.open = Number.isFinite(Number(meta.regularMarketOpen)) ? Number(meta.regularMarketOpen) : null;
                        details.prevClose = Number.isFinite(Number(meta.chartPreviousClose || meta.previousClose)) ? Number(meta.chartPreviousClose || meta.previousClose) : null;
                        details.dayHigh = Number.isFinite(Number(meta.regularMarketDayHigh)) ? Number(meta.regularMarketDayHigh) : (highs.length ? highs[highs.length - 1] : null);
                        details.dayLow = Number.isFinite(Number(meta.regularMarketDayLow)) ? Number(meta.regularMarketDayLow) : (lows.length ? lows[lows.length - 1] : null);
                        details.week52High = Number.isFinite(Number(meta.fiftyTwoWeekHigh)) ? Number(meta.fiftyTwoWeekHigh) : (highs.length ? Math.max(...highs) : null);
                        details.week52Low = Number.isFinite(Number(meta.fiftyTwoWeekLow)) ? Number(meta.fiftyTwoWeekLow) : (lows.length ? Math.min(...lows) : null);
                        details.volume = Number.isFinite(Number(meta.regularMarketVolume)) ? Number(meta.regularMarketVolume) : null;
                        details.marketCap = Number.isFinite(Number(meta.marketCap)) ? Number(meta.marketCap) : null;
                        const price = Number(meta.regularMarketPrice || closes[closes.length - 1] || 0);
                        if (price > 0 && details.volume) details.turnoverInr = Math.round(price * details.volume);
                    }
                } catch (e) {}

                const news = [];
                try {
                    const rawSymbol = symbol.includes(":") ? symbol.split(":").pop() : symbol;
                    const cleanName = assetName
                        .replace(/\s*\((?:EQ|FUTURES?|CRYPTO|EQUITY|NSE|BSE)\)\s*$/i, "")
                        .replace(/\s*\/\s*(?:USDT|USD)\s*$/i, "")
                        .trim();
                    const newsQuery = [cleanName, rawSymbol].filter((v, i, arr) => v && arr.indexOf(v) === i).join(" OR ");
                    const rssUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(`(${newsQuery}) stock market India when:3d`)}&hl=en-IN&gl=IN&ceid=IN:en`;
                    const rssRes = await fetch(rssUrl, { headers: { "User-Agent": "Mozilla/5.0" }, cf: { cacheTtl: 60 } });
                    if (rssRes.ok) {
                        const xml = await rssRes.text();
                        const blocks = xml.match(/<item>[\s\S]*?<\/item>/gi) || [];
                        for (const block of blocks.slice(0, 8)) {
                            const title = decodeHtmlEntities((block.match(/<title>([\s\S]*?)<\/title>/i)?.[1] || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/<[^>]+>/g, "").trim());
                            const link = (block.match(/<link>([\s\S]*?)<\/link>/i)?.[1] || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const pubDate = (block.match(/<pubDate>([\s\S]*?)<\/pubDate>/i)?.[1] || "").trim();
                            if (title) news.push({ title, link: link.startsWith("http") ? link : "", pubDate });
                        }
                    }
                } catch (e) {}

                const response = jsonResponse({ success: true, details, news }, 200, cors, correlationId);
                await caches.default.put(cacheKey, new Response(response.clone().body, {
                    headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=60, max-age=60" }
                })).catch(() => {});
                return response;
            }

            if (target === "historical_candles") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const sym = String(url.searchParams.get("symbol") || "").toUpperCase();
                const rawYahoo = String(url.searchParams.get("yahooSymbol") || "").trim();
                const tf = String(url.searchParams.get("timeframe") || "15m");
                const range = String(url.searchParams.get("range") || "5d");

                const allowedTf = ["15s", "1m", "5m", "15m", "1h", "1d"];
                const allowedRange = ["1d", "5d", "1mo"];
                if (!allowedTf.includes(tf) || !allowedRange.includes(range)) {
                    return jsonResponse({ success: false, code: "INVALID_CANDLE_PARAMS", error: "Invalid timeframe or range" }, 400, cors, correlationId);
                }

                let authUser = null;
                let vault = {};
                if ((request.headers.get("Authorization") || "").startsWith("Bearer ")) {
                    authUser = await verifyFirebaseToken(request, env);
                    if (authUser) vault = await safeReadVault(env, authUser.uid);
                }

                // Crypto: CoinDCX connected -> direct CoinDCX candles; else Binance
                if (sym.endsWith("USDT") || rawYahoo.endsWith("-USD")) {
                    const cleanPair = sym.replace(/-USD$/, "USDT").replace(/[^A-Z0-9]/g, "");
                    const cdxIntrv = tf === "15s" ? "1m" : tf;
                    if (vault.coindcxKey && vault.coindcxSecret) {
                        try {
                            const base = cleanPair.replace(/USDT$/, "");
                            const cdxPair = `B-${base}_USDT`;
                            const cdxRes = await fetch(`https://public.coindcx.com/market_data/candles?pair=${encodeURIComponent(cdxPair)}&interval=${encodeURIComponent(cdxIntrv)}&limit=200`);
                            const cdxArr = cdxRes.ok ? await cdxRes.json().catch(() => []) : [];
                            if (Array.isArray(cdxArr) && cdxArr.length > 0) {
                                const candles = cdxArr.map(k => ({
                                    time: Number(k.time),
                                    open: Number(k.open),
                                    high: Number(k.high),
                                    low: Number(k.low),
                                    close: Number(k.close),
                                    volume: Number(k.volume || 0)
                                })).filter(c => c.time > 0 && c.close > 0 && c.high >= c.low).sort((a, b) => a.time - b.time);
                                if (candles.length > 0) {
                                    return jsonResponse({ success: true, source: `COINDCX_${cdxIntrv.toUpperCase()}`, candles: candles.slice(-200) }, 200, cors, correlationId);
                                }
                            }
                        } catch (e) {}
                    }
                    try {
                        const bRes = await fetch(`https://api.binance.com/api/v3/klines?symbol=${encodeURIComponent(cleanPair)}&interval=${encodeURIComponent(cdxIntrv)}&limit=200`);
                        const bArr = bRes.ok ? await bRes.json().catch(() => []) : [];
                        const candles = (Array.isArray(bArr) ? bArr : []).map(k => ({
                            time: Number(k[0]), open: Number(k[1]), high: Number(k[2]), low: Number(k[3]), close: Number(k[4]), volume: Number(k[5] || 0)
                        })).filter(c => c.time > 0 && c.close > 0);
                        if (candles.length > 0) {
                            return jsonResponse({ success: true, source: `BINANCE_${cdxIntrv.toUpperCase()}`, candles }, 200, cors, correlationId);
                        }
                    } catch (e) {}
                }

                // Indian Stocks/Indices: Shoonya connected -> try Shoonya TPSeries first; else Yahoo
                if (authUser && vault.shoonyaUid && vault.shoonyaToken && tf !== "1d") {
                    try {
                        const spec = SERVER_CANONICAL_INSTRUMENTS[sym];
                        let exch = spec ? (spec.exchange === "NFO" ? "NSE" : (spec.exchange === "BFO" ? "BSE" : spec.exchange)) : (rawYahoo.endsWith(".BO") ? "BSE" : "NSE");
                        let token = spec?.token || "";
                        if (!token || token === "DYNAMIC") {
                            const sr = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/SearchScrip", {
                                method: "POST",
                                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, stext: sym, exch }))}&jKey=${vault.shoonyaToken}`
                            }).catch(() => null);
                            const sd = sr ? await sr.json().catch(() => null) : null;
                            const hit = sd?.values?.find(v => String(v.tsym).toUpperCase() === `${sym}-EQ` || String(v.symname).toUpperCase() === sym) || sd?.values?.[0];
                            if (hit?.token) { token = String(hit.token); exch = hit.exch || exch; }
                        }
                        if (token && token !== "DYNAMIC") {
                            const intrvMap = { "15s": "1", "1m": "1", "5m": "5", "15m": "15", "1h": "60" };
                            const et = Math.floor(Date.now() / 1000);
                            const st = et - (range === "1d" ? 86400 * 2 : 86400 * 6);
                            const tpRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/TPSeries", {
                                method: "POST",
                                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, exch, token, st: String(st), et: String(et), intrv: intrvMap[tf] || "15" }))}&jKey=${vault.shoonyaToken}`
                            }).catch(() => null);
                            const tpArr = tpRes ? await tpRes.json().catch(() => null) : null;
                            if (Array.isArray(tpArr) && tpArr.length > 0) {
                                const candles = tpArr.map(b => {
                                    const m = String(b.time || "").trim().match(/^(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})$/);
                                    const tMs = m ? Date.parse(`${m[3]}-${m[2]}-${m[1]}T${m[4]}:${m[5]}:${m[6]}+05:30`) : NaN;
                                    return { time: tMs, open: Number(b.into), high: Number(b.inth), low: Number(b.intl), close: Number(b.intc), volume: Number(b.intv || 0) };
                                }).filter(c => Number.isFinite(c.time) && c.close > 0 && c.high >= c.low).sort((a, b) => a.time - b.time);
                                if (candles.length > 0) {
                                    return jsonResponse({ success: true, source: `SHOONYA_${tf.toUpperCase()}`, candles: candles.slice(-200) }, 200, cors, correlationId);
                                }
                            
                            }
                        }
                    } catch (e) {}
                }

                const yahooTf = tf === "15s" ? "1m" : tf;
                const yMap = {
                    NIFTY: "^NSEI",
                    BANKNIFTY: "^NSEBANK",
                    SENSEX: "^BSESN",
                    BANKEX: "BSE-BANK.BO",
                    RELIANCE: "RELIANCE.NS",
                    HDFCBANK: "HDFCBANK.NS",
                    TATAMOTORS: "TATAMOTORS.NS",
                    TCS: "TCS.BO",
                    INFY: "INFY.BO"
                };
                const ySym = rawYahoo || yMap[sym] || (/^[A-Z0-9&-]{2,15}$/.test(sym) ? `${sym}.NS` : "");
                if (!ySym) return jsonResponse({ success: false, candles: [] }, 200, cors, correlationId);

                const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ySym)}?interval=${yahooTf}&range=${range}`, {
                    headers: { "User-Agent": "Mozilla/5.0" },
                    cf: { cacheTtl: 60 }
                });
                const j = await r.json().catch(() => null);
                const res0 = j?.chart?.result?.[0];
                const ts = res0?.timestamp || [];
                const q = res0?.indicators?.quote?.[0] || {};
                const seenTs = new Set();
                const candles = [];

                for (let i = 0; i < ts.length; i++) {
                    const tMs = Number(ts[i]) * 1000;
                    if (!tMs || seenTs.has(tMs)) continue;
                    const o = Number(q.open?.[i]), h = Number(q.high?.[i]), l = Number(q.low?.[i]), c = Number(q.close?.[i]);
                    if (o > 0 && h > 0 && l > 0 && c > 0 && h >= l) {
                        seenTs.add(tMs);
                        candles.push({
                            time: tMs,
                            open: o,
                            high: h,
                            low: l,
                            close: c,
                            volume: Math.max(0, Number(q.volume?.[i] || 0))
                        });
                    }
                }
                candles.sort((a, b) => a.time - b.time);
                return jsonResponse({ success: candles.length > 0, source: `YAHOO_${tf.toUpperCase()}`, candles: candles.slice(-200) }, 200, cors, correlationId);
            }
            if (target === "get_risk_state") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                const gRes = await doStub.fetch("https://guard/get-risk-state", { method: "POST" });
                return jsonResponse(await gRes.json(), 200, cors, correlationId);
            }

            if (target === "set_risk_config") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const { capitalInr, maxDailyLossPct, maxTradesPerDay } = body;
                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                const rRes = await doStub.fetch("https://guard/set-risk-config", {
                    method: "POST",
                    body: JSON.stringify({ ownerUid: authUser.uid, capitalInr, maxDailyLossPct, maxTradesPerDay, isAdminOverride: false })
                });
                return jsonResponse(await rRes.json(), rRes.status, cors, correlationId);
            }

            if (target === "sync_daily_pnl") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const snap = await computeServerBrokerPnlInr(env, authUser.uid);
                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                const endpoint = snap !== null && !snap.incompletePnl ? "https://guard/sync-pnl" : "https://guard/get-risk-state";
                const gRes = await doStub.fetch(endpoint, {
                    method: "POST",
                    body: JSON.stringify(snap !== null && !snap.incompletePnl ? { ownerUid: authUser.uid, dailyPnlInr: snap.totalInr } : {})
                });
                const gData = await gRes.json();
                return jsonResponse({ ...gData, incompletePnl: snap?.incompletePnl === true }, 200, cors, correlationId);
            }

            if (target === "broker_balances") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);

                let shoonyaRes = { linked: !!(vault.shoonyaUid && vault.shoonyaToken), status: "NOT_CONNECTED" };
                let coindcxRes = { linked: !!(vault.coindcxKey && vault.coindcxSecret), status: "NOT_CONNECTED" };

                await Promise.allSettled([
                    (async () => {
                        if (!shoonyaRes.linked) return;
                        const r = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/Limits", {
                            method: "POST",
                            headers: { "Content-Type": "application/x-www-form-urlencoded" },
                            body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, actid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
                        });
                        const d = await r.json().catch(() => null);
                        if (d?.stat === "Ok") {
                            shoonyaRes = {
                                linked: true,
                                status: "LIVE",
                                cash: Number(d.cash || 0) + Number(d.collateral || 0) + Number(d.payin || 0),
                                marginUsed: Number(d.marginused || d.ordermargin || 0),
                                updatedAt: Date.now()
                            };
                        } else {
                            shoonyaRes.status = "ERROR";
                        }
                    })(),
                    (async () => {
                        if (!coindcxRes.linked) return;
                        const payloadStr = JSON.stringify({ timestamp: Date.now() });
                        const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);
                        let spotList = null;
                        let futuresList = null;
                        let spotError = "";
                        let futuresError = "";

                        const commonHeaders = {
                            "Content-Type": "application/json",
                            "X-AUTH-APIKEY": vault.coindcxKey,
                            "X-AUTH-SIGNATURE": sig
                        };

                        await Promise.all([
                            (async () => {
                                try {
                                    const r = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/users/balances", {
                                        method: "POST",
                                        headers: commonHeaders,
                                        body: payloadStr
                                    });
                                    const d = await r.json().catch(() => null);
                                    if (Array.isArray(d)) spotList = d;
                                    else spotError = d?.message || d?.error || `HTTP ${r.status}`;
                                } catch (err) {
                                    spotError = err?.message || "Spot balance request failed";
                                }
                            })(),
                            (async () => {
                                try {
                                    const r = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/wallets", {
                                        method: "POST",
                                        headers: commonHeaders,
                                        body: payloadStr
                                    });
                                    const d = await r.json().catch(() => null);
                                    if (Array.isArray(d) || (d && typeof d === "object")) futuresList = d;
                                    else futuresError = d?.message || d?.error || `HTTP ${r.status}`;
                                } catch (err) {
                                    futuresError = err?.message || "Futures wallet request failed";
                                }
                            })()
                        ]);

                        const spotUsdt = Array.isArray(spotList) ? spotList.find(b => b.currency === "USDT" || b.currency === "USDT_FUT") : null;
                        const spotInr = Array.isArray(spotList) ? spotList.find(b => b.currency === "INR") : null;
                        const futuresRows = Array.isArray(futuresList) ? futuresList : (futuresList ? Object.values(futuresList) : []);
                        const futureUsdt = futuresRows.find(b => String(b.currency || b.asset || b.short_name || "").toUpperCase() === "USDT");
                        const futureInr = futuresRows.find(b => String(b.currency || b.asset || b.short_name || "").toUpperCase() === "INR");

                        const usdtBalance = Number(spotUsdt?.balance || spotUsdt?.locked || 0) +
                            Number(futureUsdt?.balance || futureUsdt?.available_balance || futureUsdt?.wallet_balance || 0);
                        const inrBalance = Number(spotInr?.balance || spotInr?.locked || 0) +
                            Number(futureInr?.balance || futureInr?.available_balance || futureInr?.wallet_balance || 0);

                        if (spotList || futuresList) {
                            coindcxRes = {
                                linked: true,
                                status: "LIVE",
                                usdtBalance,
                                inrBalance,
                                updatedAt: Date.now(),
                                spotError: spotError || null,
                                futuresError: futuresError || null
                            };
                        } else {
                            coindcxRes.status = "ERROR";
                            coindcxRes.errorReason = spotError && futuresError
                                ? `Spot: ${spotError}; Futures: ${futuresError}`
                                : (spotError || futuresError || "CoinDCX balance unavailable");
                        }
                    })()
                ]);

                return jsonResponse({ success: true, shoonya: shoonyaRes, coindcx: coindcxRes }, 200, cors, correlationId);
            }

            if (target === "broker_positions") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);
                const normalizedPositions = [];
                await Promise.allSettled([
                    (async () => {
                        if (!vault.shoonyaUid || !vault.shoonyaToken) return;
                        const r = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PositionBook", {
                            method: "POST",
                            headers: { "Content-Type": "application/x-www-form-urlencoded" },
                            body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, actid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
                        });
                        const list = await r.json().catch(() => []);
                        if (Array.isArray(list)) {
                            list.filter(p => Number(p.netqty || 0) !== 0).forEach(p => {
                                normalizedPositions.push({
                                    id: `SHOONYA_${p.exch}_${p.token}`,
                                    broker: "SHOONYA",
                                    exchange: p.exch,
                                    token: p.token,
                                    tsym: p.tsym,
                                    qty: Number(p.netqty),
                                    avgPrice: Number(p.netavgprc || p.buyavgprc || 0),
                                    rpnl: Number(p.rpnl || 0),
                                    upnl: Number(p.urmtom || 0),
                                    quoteCurrency: "INR",
                                    assetClass: "EQUITY_DERIVATIVE"
                                });
                            });
                        }
                    })(),
                    (async () => {
                        if (!vault.coindcxKey || !vault.coindcxSecret) return;
                        const list = await getCoinDcxOpenPositionsWithMark(env, authUser.uid, vault);
                        if (Array.isArray(list)) {
                            list.forEach(p => {
                                const pair = String(p.market || "").replace("B-", "").replace("_", "").toUpperCase();
                                normalizedPositions.push({
                                    id: `COINDCX_${pair}`,
                                    broker: "COINDCX",
                                    exchange: "COINDCX",
                                    token: pair,
                                    tsym: pair,
                                    marketPair: p.market,
                                    qty: p.signedQty,
                                    avgPrice: p.avgPrice,
                                    rpnl: p.rpnl,
                                    upnl: p.upnl,
                                    incomplete: p.incomplete,
                                    quoteCurrency: p.quoteCurrency,
                                    assetClass: "CRYPTO_FUTURES"
                                });
                            });
                        }
                    })()
                ]);

                return jsonResponse({ success: true, positions: normalizedPositions }, 200, cors, correlationId);
            }

            if (target === "place_order") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ success: false, code: "KYC_OR_AUTH_REJECTED", error: kycChk.message }, kycChk.status, cors, correlationId);

                const clientOrderId = request.headers.get("X-Client-Order-Id");
                if (typeof clientOrderId !== "string" || !/^[A-Za-z0-9_-]{8,64}$/.test(clientOrderId)) {
                    return jsonResponse({ success: false, code: "INVALID_IDEMPOTENCY_KEY", error: "Valid X-Client-Order-Id header (8-64 chars) required" }, 400, cors, correlationId);
                }

                if (!body || typeof body !== "object" || Array.isArray(body)) {
                    return jsonResponse({ success: false, code: "INVALID_PAYLOAD", error: "Payload must be JSON object" }, 400, cors, correlationId);
                }

                const mode = body.mode;
                if (typeof mode !== "string" || (mode !== "PAPER" && mode !== "LIVE")) {
                    return jsonResponse({ success: false, code: "INVALID_TRADING_MODE", error: "Trading mode must strictly be PAPER or LIVE" }, 400, cors, correlationId);
                }
                if (mode === "LIVE" && env.LIVE_TRADING_ENABLED !== "true") {
                    return jsonResponse({
                        success: false,
                        code: "LIVE_TRADING_DISABLED",
                        state: "BLOCKED_PHASE0_FREEZE",
                        message: "Live automated trading frozen on server (Safe Mode). Use PAPER mode."
                    }, 403, cors, correlationId);
                }

                const rawSym = body.symbol;
                const side = body.side;
                const operationType = body.operationType;

                if (typeof rawSym !== "string" || !rawSym.trim()) {
                    return jsonResponse({ success: false, code: "INVALID_SYMBOL", error: "Symbol is required" }, 400, cors, correlationId);
                }
                const upperSym = rawSym.trim().toUpperCase();
                let spec = SERVER_CANONICAL_INSTRUMENTS[upperSym];
                if (!spec) {
                    if (/^[A-Z0-9]{2,15}USDT$/.test(upperSym)) {
                        const base = upperSym.replace(/USDT$/, "");
                        const serverCap = Math.min(10, Math.max(1, Number(env.MAX_CRYPTO_LEVERAGE || 5)));
                        spec = { broker: "COINDCX", marketPair: `B-${base}_USDT`, minQty: 0.001, maxQty: 500000, maxLeverage: 10 };
                    } else if (/^[A-Z0-9&-]{2,20}$/.test(upperSym)) {
                        const ex = body.exchange === "BSE" ? "BSE" : "NSE";
                        spec = { broker: "SHOONYA", exchange: ex, segment: "EQ", lotSize: 1, maxQty: 5000, tickSize: 0.05, token: "DYNAMIC" };
                    }
                }
                if (!spec) return jsonResponse({ success: false, code: "UNSUPPORTED_SYMBOL", error: `Unsupported symbol: ${upperSym}` }, 400, cors, correlationId);

                if (side !== "BUY" && side !== "SELL") return jsonResponse({ success: false, code: "INVALID_SIDE", error: "Side must be BUY or SELL" }, 400, cors, correlationId);
                if (operationType !== "ENTRY" && operationType !== "EXIT") return jsonResponse({ success: false, code: "INVALID_OPERATION", error: "operationType must be ENTRY or EXIT" }, 400, cors, correlationId);

                if (typeof body.qty !== "number" || !Number.isFinite(body.qty) || body.qty <= 0 || body.qty > spec.maxQty) {
                    return jsonResponse({ success: false, code: "INVALID_QUANTITY", error: "Invalid or out-of-range quantity" }, 400, cors, correlationId);
                }
                const numQty = body.qty;

                if (spec.broker === "SHOONYA" && (!Number.isInteger(numQty) || (spec.lotSize > 1 && numQty % spec.lotSize !== 0))) {
                    return jsonResponse({ success: false, code: "INVALID_LOT_SIZE", error: `Quantity must be integer multiple of lot size ${spec.lotSize}` }, 400, cors, correlationId);
                }

                const lev = body.leverage === undefined ? 1 : Number(body.leverage);
                if (spec.broker === "COINDCX") {
                    if (numQty < spec.minQty) return jsonResponse({ success: false, code: "INVALID_MIN_QTY", error: `Minimum quantity is ${spec.minQty}` }, 400, cors, correlationId);
                    if (typeof body.leverage === "boolean" || typeof lev !== "number" || !Number.isFinite(lev) || lev < 1 || lev > spec.maxLeverage) {
                        return jsonResponse({ success: false, code: "INVALID_LEVERAGE", error: `Leverage must be between 1 and ${spec.maxLeverage}` }, 400, cors, correlationId);
                    }
                }

                const slRaw = body.slPrice;
                const slValid = (typeof slRaw === "number" || (typeof slRaw === "string" && slRaw.trim() !== "")) && Number.isFinite(Number(slRaw)) && Number(slRaw) > 0;

                if (operationType === "ENTRY") {
                    if (!(await requirePinSession(request, env, authUser.uid))) {
                        return jsonResponse({ success: false, code: "PIN_SESSION_REQUIRED", error: "Server PIN unlock required for ENTRY (X-Pin-Token)" }, 403, cors, correlationId);
                    }
                    if (!slValid) {
                        return jsonResponse({ success: false, code: "SL_REQUIRED", error: "Stop-Loss price is mandatory for ENTRY." }, 400, cors, correlationId);
                    }
                }
                const slPrice = slValid ? Number(slRaw) : null;

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                let vault = null;
                let verifiedContract = null;

                if (mode === "LIVE") {
                    vault = await safeReadVault(env, authUser.uid);

                    if (operationType === "ENTRY") {
                        const liveSnap = await computeServerBrokerPnlInr(env, authUser.uid);
                        if (liveSnap?.incompletePnl) {
                            return jsonResponse({ success: false, code: "INCOMPLETE_PNL_CHECK", error: "Cannot verify live floating P&L/FX rate; new entries temporarily blocked." }, 503, cors, correlationId);
                        }
                        if (liveSnap !== null) {
                            await doStub.fetch("https://guard/sync-pnl", {
                                method: "POST",
                                body: JSON.stringify({ ownerUid: authUser.uid, dailyPnlInr: liveSnap.totalInr })
                            });
                        }
                    }

                    if (spec.broker === "SHOONYA") {
                        if (!vault.shoonyaUid || !vault.shoonyaToken) {
                            return jsonResponse({ success: false, code: "SHOONYA_UNLINKED", error: "Shoonya credentials missing" }, 400, cors, correlationId);
                        }
                        verifiedContract = await resolveVerifiedShoonyaContract(env, authUser.uid, vault, upperSym, spec, getIstDateStr());
                        if (!verifiedContract) {
                            return jsonResponse({ success: false, code: "INSTRUMENT_MASTER_UNAVAILABLE", error: "Could not verify valid Shoonya contract from Instrument Master." }, 503, cors, correlationId);
                        }

                        if (operationType === "EXIT") {
                            const netQty = await getShoonyaPositionNetQty(env, authUser.uid, vault, verifiedContract.exch, verifiedContract.tsym, verifiedContract.token);
                            if (netQty === null) return jsonResponse({ success: false, code: "POSITION_CHECK_FAILED", error: "Failed to read Shoonya PositionBook" }, 502, cors, correlationId);
                            if (netQty === 0) return jsonResponse({ success: false, code: "NO_OPEN_POSITION", error: "No open Shoonya position to exit" }, 400, cors, correlationId);
                            if ((netQty > 0 && side !== "SELL") || (netQty < 0 && side !== "BUY")) {
                                return jsonResponse({ success: false, code: "WRONG_EXIT_SIDE", error: "EXIT side mismatches open position direction" }, 400, cors, correlationId);
                            }
                            if (numQty > Math.abs(netQty)) {
                                return jsonResponse({ success: false, code: "EXIT_QTY_EXCEEDS_POSITION", error: `EXIT qty exceeds open position (${Math.abs(netQty)})` }, 400, cors, correlationId);
                            }
                        }
                    }

                    if (spec.broker === "COINDCX") {
                        if (!vault.coindcxKey || !vault.coindcxSecret) {
                            return jsonResponse({ success: false, code: "COINDCX_UNLINKED", error: "CoinDCX credentials missing" }, 400, cors, correlationId);
                        }
                        if (operationType === "EXIT") {
                            const dcxPosList = await getCoinDcxOpenPositionsWithMark(env, authUser.uid, vault);
                            if (!Array.isArray(dcxPosList)) return jsonResponse({ success: false, code: "POSITION_CHECK_FAILED", error: "Failed to read CoinDCX positions" }, 502, cors, correlationId);
                            const pos = dcxPosList.find(p => p.market === spec.marketPair || p.market.replace("B-", "").replace("_", "").toUpperCase() === upperSym);
                            const netQty = pos ? pos.signedQty : 0;
                            if (netQty === 0) return jsonResponse({ success: false, code: "NO_OPEN_POSITION", error: "No open CoinDCX position to exit" }, 400, cors, correlationId);
                            if ((netQty > 0 && side !== "SELL") || (netQty < 0 && side !== "BUY")) {
                                return jsonResponse({ success: false, code: "WRONG_EXIT_SIDE", error: "EXIT side mismatches open position direction" }, 400, cors, correlationId);
                            }
                            if (numQty > Math.abs(netQty)) {
                                return jsonResponse({ success: false, code: "EXIT_QTY_EXCEEDS_POSITION", error: `EXIT qty exceeds open position (${Math.abs(netQty)})` }, 400, cors, correlationId);
                            }
                        }
                    }
                }

                const guardRes = await doStub.fetch("https://guard/precheck-and-lock", {
                    method: "POST",
                    body: JSON.stringify({ ownerUid: authUser.uid, clientOrderId, operationType, symbol: upperSym, side, qty: numQty, mode })
                });
                const guardData = await guardRes.json();
                if (!guardRes.ok || !guardData.allowed) {
                    return jsonResponse({ success: false, code: "RISK_OR_IDEM_BLOCKED", ...guardData }, guardRes.status, cors, correlationId);
                }

                if (mode === "PAPER") {
                    const paperId = `PPR_${Date.now()}`;
                    await doStub.fetch("https://guard/update-order-state", {
                        method: "POST",
                        body: JSON.stringify({ clientOrderId, state: "PAPER_FILLED", brokerOrderId: paperId, filledQty: numQty, paperUpdate: { symbol: upperSym, side, qty: numQty } })
                    });
                    auditLog("INFO", correlationId, "PAPER_ORDER_FILLED", { uid: authUser.uid, symbol: upperSym, side, qty: numQty, operationType });
                    return jsonResponse({
                        success: true, mode: "PAPER", state: "PAPER_FILLED",
                        clientOrderId, brokerOrderId: paperId,
                        protectionStatus: operationType === "ENTRY" ? "SIMULATED_SL" : "PAPER_EXIT",
                        risk: guardData.risk
                    }, 200, cors, correlationId);
                }

                if (mode !== "LIVE" || env.LIVE_TRADING_ENABLED !== "true") {
                    await doStub.fetch("https://guard/rollback-lock", {
                        method: "POST",
                        body: JSON.stringify({ clientOrderId, operationType, state: "BLOCKED", reason: "Live gate closed" })
                    });
                    return jsonResponse({ success: false, code: "LIVE_TRADING_DISABLED", message: "Live dispatch blocked" }, 403, cors, correlationId);
                }

                if (spec.broker === "SHOONYA") {
                    const sRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PlaceOrder", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: `jData=${encodeURIComponent(JSON.stringify({
                            uid: vault.shoonyaUid, actid: vault.shoonyaUid, exch: verifiedContract.exch, tsym: verifiedContract.tsym,
                            qty: String(numQty), prc: "0", prd: "I", trantype: side === "BUY" ? "B" : "S", prctyp: "MKT", ret: "DAY"
                        }))}&jKey=${vault.shoonyaToken}`
                    });
                    const d = await sRes.json().catch(() => ({}));
                    if (d?.stat === "Ok" && d?.norenordno) {
                        const entryChk = await verifyShoonyaOrderStatus(env, authUser.uid, vault, d.norenordno, 3);
                        if (entryChk.status === "REJECTED" || entryChk.status === "CANCELLED") {
                            await doStub.fetch("https://guard/rollback-lock", {
                                method: "POST",
                                body: JSON.stringify({ clientOrderId, operationType, state: entryChk.status, reason: entryChk.rejectReason || "Shoonya entry not filled" })
                            });
                            return jsonResponse({ success: false, state: entryChk.status, message: entryChk.rejectReason || "Entry rejected by Shoonya" }, 400, cors, correlationId);
                        }

                        const actualFilled = entryChk.filledQty > 0 ? entryChk.filledQty : (entryChk.status === "FILLED" ? numQty : 0);

                        if (operationType === "EXIT") {
                            const remQty = await getShoonyaPositionNetQty(env, authUser.uid, vault, verifiedContract.exch, verifiedContract.tsym, verifiedContract.token);
                            await doStub.fetch("https://guard/update-order-state", {
                                method: "POST",
                                body: JSON.stringify({ clientOrderId, state: entryChk.status, brokerOrderId: d.norenordno, avgPrice: entryChk.avgPrice, filledQty: actualFilled })
                            });
                            return jsonResponse({
                                success: entryChk.status === "FILLED",
                                mode: "LIVE",
                                state: entryChk.status,
                                clientOrderId,
                                brokerOrderId: d.norenordno,
                                protectionStatus: remQty === 0 ? "EXIT_VERIFIED_CLOSED" : "EXIT_SUBMITTED",
                                risk: guardData.risk
                            }, 200, cors, correlationId);
                        }

                        if (actualFilled <= 0) {
                            await doStub.fetch("https://guard/update-order-state", {
                                method: "POST",
                                body: JSON.stringify({ clientOrderId, state: "OPEN", brokerOrderId: d.norenordno, scheduleAlarm: true })
                            });
                            return jsonResponse({
                                success: true, mode: "LIVE", state: "OPEN",
                                clientOrderId, brokerOrderId: d.norenordno,
                                protectionStatus: "AWAITING_FILL_FOR_SL",
                                risk: guardData.risk
                            }, 200, cors, correlationId);
                        }

                        const slSide = side === "BUY" ? "S" : "B";
                        const roundedTrg = roundToTick(slPrice, verifiedContract.tickSize);
                        const roundedPrc = roundToTick(slSide === "S" ? roundedTrg * 0.995 : roundedTrg * 1.005, verifiedContract.tickSize);

                        const slRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PlaceOrder", {
                            method: "POST",
                            headers: { "Content-Type": "application/x-www-form-urlencoded" },
                            body: `jData=${encodeURIComponent(JSON.stringify({
                                uid: vault.shoonyaUid, actid: vault.shoonyaUid, exch: verifiedContract.exch, tsym: verifiedContract.tsym,
                                qty: String(actualFilled), prc: String(roundedPrc), trgprc: String(roundedTrg),
                                prd: "I", trantype: slSide, prctyp: "SL-LMT", ret: "DAY"
                            }))}&jKey=${vault.shoonyaToken}`
                        });
                        const slJson = await slRes.json().catch(() => ({}));

                        let slActive = false;
                        if (slJson?.stat === "Ok" && slJson?.norenordno) {
                            const slChk = await verifyShoonyaOrderStatus(env, authUser.uid, vault, slJson.norenordno, 2);
                            slActive = slChk.status === "OPEN" || slChk.status === "FILLED";
                        }

                        if (slActive) {
                            await doStub.fetch("https://guard/update-order-state", {
                                method: "POST",
                                body: JSON.stringify({ clientOrderId, state: entryChk.status, brokerOrderId: d.norenordno, avgPrice: entryChk.avgPrice, filledQty: actualFilled, scheduleAlarm: true })
                            });
                            return jsonResponse({
                                success: true, mode: "LIVE", state: entryChk.status,
                                clientOrderId, brokerOrderId: d.norenordno,
                                slOrderId: slJson.norenordno,
                                protectionStatus: "BROKER_SL_ACTIVE",
                                risk: guardData.risk
                            }, 200, cors, correlationId);
                        }

                        const emgRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PlaceOrder", {
                            method: "POST",
                            headers: { "Content-Type": "application/x-www-form-urlencoded" },
                            body: `jData=${encodeURIComponent(JSON.stringify({
                                uid: vault.shoonyaUid, actid: vault.shoonyaUid, exch: verifiedContract.exch, tsym: verifiedContract.tsym,
                                qty: String(actualFilled), prc: "0", prd: "I", trantype: slSide, prctyp: "MKT", ret: "DAY"
                            }))}&jKey=${vault.shoonyaToken}`
                        });
                        const emgJson = await emgRes.json().catch(() => ({}));

                        let emgClosed = false;
                        if (emgJson?.stat === "Ok" && emgJson?.norenordno) {
                            const emgChk = await verifyShoonyaOrderStatus(env, authUser.uid, vault, emgJson.norenordno, 3);
                            const posAfter = await getShoonyaPositionNetQty(env, authUser.uid, vault, verifiedContract.exch, verifiedContract.tsym, verifiedContract.token);
                            emgClosed = emgChk.status === "FILLED" && posAfter === 0;
                        }

                        if (emgClosed) {
                            await doStub.fetch("https://guard/update-order-state", {
                                method: "POST",
                                body: JSON.stringify({ clientOrderId, state: "EMERGENCY_EXITED_VERIFIED", brokerOrderId: d.norenordno })
                            });
                            return jsonResponse({
                                success: false, state: "EMERGENCY_EXITED_VERIFIED",
                                clientOrderId, brokerOrderId: d.norenordno,
                                protectionStatus: "EMERGENCY_EXITED_VERIFIED",
                                message: "SL placement failed; emergency exit verified closed in PositionBook."
                            }, 409, cors, correlationId);
                        }

                        await doStub.fetch("https://guard/critical-unprotected-lock", {
                            method: "POST",
                            body: JSON.stringify({ reason: "CRITICAL_UNPROTECTED_POSITION" })
                        });
                        return jsonResponse({
                            success: false, state: "CRITICAL_UNPROTECTED_POSITION",
                            clientOrderId, brokerOrderId: d.norenordno,
                            protectionStatus: "UNPROTECTED_OPEN_POSITION",
                            message: "CRITICAL: SL failed and emergency exit could not be verified closed in PositionBook! New entries hard-locked."
                        }, 502, cors, correlationId);
                    }

                    await doStub.fetch("https://guard/rollback-lock", {
                        method: "POST",
                        body: JSON.stringify({ clientOrderId, operationType, state: "REJECTED", reason: d?.emsg || "Shoonya rejected" })
                    });
                    return jsonResponse({ success: false, state: "REJECTED", message: d?.emsg || "Shoonya rejected order" }, 400, cors, correlationId);
                }

                if (spec.broker === "COINDCX") {
                    const cleanOrder = {
                        side: side.toLowerCase(),
                        pair: spec.marketPair,
                        total_quantity: numQty,
                        leverage: lev,
                        order_type: "market_order",
                        margin_currency_short_name: "USDT",
                        client_order_id: clientOrderId
                    };
                    if (operationType === "ENTRY" && slPrice) cleanOrder.stop_loss_price = slPrice;
                    const payloadStr = JSON.stringify({ timestamp: Date.now(), order: cleanOrder });
                    const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);

                    const dRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders/create", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": sig },
                        body: payloadStr
                    });
                    const d = await dRes.json().catch(() => ({}));
                    const ordId = d?.id || d?.order_id || (Array.isArray(d) ? d[0]?.id : null);
                    if (dRes.ok && ordId) {
                        await doStub.fetch("https://guard/update-order-state", {
                            method: "POST",
                            body: JSON.stringify({ clientOrderId, state: "ACCEPTED", brokerOrderId: ordId, scheduleAlarm: true })
                        });
                        return jsonResponse({
                            success: true, mode: "LIVE", state: "ACCEPTED",
                            clientOrderId, brokerOrderId: ordId,
                            protectionStatus: cleanOrder.stop_loss_price ? "SL_REQUESTED_WITH_ORDER" : "EXIT_ORDER",
                            risk: guardData.risk
                        }, 200, cors, correlationId);
                    }
                    await doStub.fetch("https://guard/rollback-lock", {
                        method: "POST",
                        body: JSON.stringify({ clientOrderId, operationType, state: "REJECTED", reason: d?.message || "CoinDCX rejected" })
                    });
                    return jsonResponse({ success: false, state: "REJECTED", message: d?.message || "CoinDCX rejected order" }, 400, cors, correlationId);
                }
            }

            if (target === "fast_exit") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ success: false, error: kycChk.message }, kycChk.status, cors, correlationId);

                const clientOrderId = request.headers.get("X-Client-Order-Id");
                if (typeof clientOrderId !== "string" || !/^[A-Za-z0-9_-]{8,64}$/.test(clientOrderId)) {
                    return jsonResponse({ success: false, code: "INVALID_IDEMPOTENCY_KEY", error: "Valid X-Client-Order-Id required" }, 400, cors, correlationId);
                }

                const mode = body.mode === "PAPER" ? "PAPER" : "LIVE";
                if (mode === "LIVE" && env.LIVE_TRADING_ENABLED !== "true") {
                    return jsonResponse({ success: false, code: "LIVE_TRADING_DISABLED", error: "Live execution disabled" }, 403, cors, correlationId);
                }

                const upperSym = String(body.symbol || "").trim().toUpperCase();
                let spec = SERVER_CANONICAL_INSTRUMENTS[upperSym];
                if (!spec) {
                    if (/^[A-Z0-9]{2,15}USDT$/.test(upperSym)) {
                        const base = upperSym.replace(/USDT$/, "");
                        spec = { broker: "COINDCX", marketPair: `B-${base}_USDT`, minQty: 0.001, maxQty: 500000, maxLeverage: 10 };
                    } else if (/^[A-Z0-9&-]{2,20}$/.test(upperSym)) {
                        const ex = body.exchange === "BSE" ? "BSE" : "NSE";
                        spec = { broker: "SHOONYA", exchange: ex, segment: "EQ", lotSize: 1, maxQty: 5000, tickSize: 0.05, token: "DYNAMIC" };
                    }
                }
                if (!spec) return jsonResponse({ success: false, error: `Unsupported symbol: ${upperSym}` }, 400, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);
                let cancelledOrdersCount = 0;

                if (mode === "PAPER") {
                    const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                    const gRes = await doStub.fetch("https://guard/update-order-state", {
                        method: "POST",
                        body: JSON.stringify({ clientOrderId, state: "PAPER_FILLED", brokerOrderId: `PPR_EXIT_${Date.now()}`, paperUpdate: { symbol: upperSym, side: body.side === "BUY" ? "BUY" : "SELL", qty: Number(body.qty || 0) } })
                    });
                    return jsonResponse({ success: true, mode: "PAPER", state: "PAPER_FILLED", cancelledOrdersCount: 0, netZeroVerified: true, ...(await gRes.json()) }, 200, cors, correlationId);
                }

                if (spec.broker === "SHOONYA") {
                    if (!vault.shoonyaUid || !vault.shoonyaToken) return jsonResponse({ success: false, error: "Shoonya unlinked" }, 400, cors, correlationId);
                    const verifiedContract = await resolveVerifiedShoonyaContract(env, authUser.uid, vault, upperSym, spec, getIstDateStr());
                    if (!verifiedContract) return jsonResponse({ success: false, error: "Contract master unavailable" }, 503, cors, correlationId);

                    // Step 1: Cancel all pending/open protective orders for this contract first
                    const obRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/OrderBook", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
                    }).catch(() => null);
                    const orders = obRes ? await obRes.json().catch(() => []) : [];
                    if (Array.isArray(orders)) {
                        const pending = orders.filter(o =>
                            o.exch === verifiedContract.exch &&
                            (o.tsym === verifiedContract.tsym || String(o.token) === String(verifiedContract.token)) &&
                            ["OPEN", "PENDING", "TRIGGER_PENDING"].includes(String(o.status || "").toUpperCase())
                        );
                        for (const pOrd of pending) {
                            const cRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/CancelOrder", {
                                method: "POST",
                                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, norenordno: pOrd.norenordno }))}&jKey=${vault.shoonyaToken}`
                            }).catch(() => null);
                            const cJson = cRes ? await cRes.json().catch(() => null) : null;
                            if (cJson?.stat === "Ok") cancelledOrdersCount++;
                        }
                        if (pending.length > 0) await new Promise(r => setTimeout(r, 350));
                    }

                    // Step 2: Re-read net position from PositionBook
                    const netBefore = await getShoonyaPositionNetQty(env, authUser.uid, vault, verifiedContract.exch, verifiedContract.tsym, verifiedContract.token);
                    if (netBefore === null) return jsonResponse({ success: false, error: "Could not read PositionBook" }, 502, cors, correlationId);
                    if (netBefore === 0) {
                        return jsonResponse({ success: true, state: "ALREADY_CLOSED_NET_ZERO", cancelledOrdersCount, closedQty: 0, netQtyAfter: 0, netZeroVerified: true }, 200, cors, correlationId);
                    }

                    // Step 3: Close ONLY remaining net quantity
                    const closeSide = netBefore > 0 ? "S" : "B";
                    const closeQty = Math.abs(netBefore);
                    const pRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PlaceOrder", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: `jData=${encodeURIComponent(JSON.stringify({
                            uid: vault.shoonyaUid, actid: vault.shoonyaUid, exch: verifiedContract.exch, tsym: verifiedContract.tsym,
                            qty: String(closeQty), prc: "0", prd: "I", trantype: closeSide, prctyp: "MKT", ret: "DAY"
                        }))}&jKey=${vault.shoonyaToken}`
                    });
                    const pJson = await pRes.json().catch(() => ({}));
                    if (pJson?.stat !== "Ok" || !pJson?.norenordno) {
                        return jsonResponse({ success: false, state: "EXIT_REJECTED", cancelledOrdersCount, message: pJson?.emsg || "Shoonya exit rejected" }, 400, cors, correlationId);
                    }

                    // Step 4: Verify fill & net zero
                    const chk = await verifyShoonyaOrderStatus(env, authUser.uid, vault, pJson.norenordno, 3);
                    const netAfter = await getShoonyaPositionNetQty(env, authUser.uid, vault, verifiedContract.exch, verifiedContract.tsym, verifiedContract.token);
                    return jsonResponse({
                        success: netAfter === 0 || chk.status === "FILLED",
                        mode: "LIVE",
                        state: netAfter === 0 ? "EXIT_VERIFIED_NET_ZERO" : chk.status,
                        brokerOrderId: pJson.norenordno,
                        cancelledOrdersCount,
                        closedQty: closeQty,
                        netQtyAfter: netAfter,
                        netZeroVerified: netAfter === 0
                    }, 200, cors, correlationId);
                }

                if (spec.broker === "COINDCX") {
                    if (!vault.coindcxKey || !vault.coindcxSecret) return jsonResponse({ success: false, error: "CoinDCX unlinked" }, 400, cors, correlationId);

                    // Step 1: Cancel all open futures orders for this marketPair
                    const openOrdPayload = JSON.stringify({ timestamp: Date.now(), status: "open", page: "1", size: "50", margin_currency_short_name: ["USDT", "INR"] });
                    const openSig = await generateHmacSha256(vault.coindcxSecret, openOrdPayload);
                    const oRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": openSig },
                        body: openOrdPayload
                    }).catch(() => null);
                    const openList = oRes ? await oRes.json().catch(() => []) : [];
                    if (Array.isArray(openList)) {
                        const matching = openList.filter(o => o.pair === spec.marketPair && o.id);
                        for (const ord of matching) {
                            const cPayload = JSON.stringify({ timestamp: Math.floor(Date.now() / 1000), id: ord.id });
                            const cSig = await generateHmacSha256(vault.coindcxSecret, cPayload);
                            const cRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders/cancel", {
                                method: "POST",
                                headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": cSig },
                                body: cPayload
                            }).catch(() => null);
                            if (cRes && cRes.ok) cancelledOrdersCount++;
                        }
                        if (matching.length > 0) await new Promise(r => setTimeout(r, 350));
                    }

                    // Step 2: Re-read net position
                    const posList = await getCoinDcxOpenPositionsWithMark(env, authUser.uid, vault);
                    if (!Array.isArray(posList)) return jsonResponse({ success: false, error: "Failed to read CoinDCX positions" }, 502, cors, correlationId);
                    const pos = posList.find(p => p.market === spec.marketPair || p.market.replace("B-", "").replace("_", "").toUpperCase() === upperSym);
                    if (!pos || pos.signedQty === 0) {
                        return jsonResponse({ success: true, state: "ALREADY_CLOSED_NET_ZERO", cancelledOrdersCount, closedQty: 0, netQtyAfter: 0, netZeroVerified: true }, 200, cors, correlationId);
                    }

                    // Step 3: Native CoinDCX Position Exit (or fallback remaining-qty market close)
                    const closeQty = Math.abs(pos.signedQty);
                    let exitOk = false;
                    let exitOrderId = `CDX_EXIT_${Date.now()}`;

                    if (pos.raw?.id) {
                        const exPayload = JSON.stringify({ timestamp: Math.floor(Date.now() / 1000), id: pos.raw.id });
                        const exSig = await generateHmacSha256(vault.coindcxSecret, exPayload);
                        const exRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/positions/exit", {
                            method: "POST",
                            headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": exSig },
                            body: exPayload
                        }).catch(() => null);
                        if (exRes && exRes.ok) {
                            const exJson = await exRes.json().catch(() => ({}));
                            exitOk = true;
                            if (exJson?.group_id) exitOrderId = exJson.group_id;
                        }
                    }

                    if (!exitOk) {
                        const closeSide = pos.signedQty > 0 ? "sell" : "buy";
                        const mPayload = JSON.stringify({
                            timestamp: Date.now(),
                            order: {
                                side: closeSide, pair: spec.marketPair, total_quantity: closeQty,
                                order_type: "market_order", margin_currency_short_name: pos.quoteCurrency || "USDT",
                                client_order_id: clientOrderId
                            }
                        });
                        const mSig = await generateHmacSha256(vault.coindcxSecret, mPayload);
                        const mRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders/create", {
                            method: "POST",
                            headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": mSig },
                            body: mPayload
                        });
                        const mJson = await mRes.json().catch(() => ({}));
                        if (!mRes.ok) return jsonResponse({ success: false, state: "EXIT_REJECTED", cancelledOrdersCount, message: mJson?.message || "CoinDCX exit failed" }, 400, cors, correlationId);
                        exitOrderId = mJson?.id || exitOrderId;
                    }

                    // Step 4: Verify net position zero
                    await new Promise(r => setTimeout(r, 600));
                    const afterList = await getCoinDcxOpenPositionsWithMark(env, authUser.uid, vault);
                    const afterPos = Array.isArray(afterList) ? afterList.find(p => p.market === spec.marketPair || p.market.replace("B-", "").replace("_", "").toUpperCase() === upperSym) : null;
                    const netAfter = afterPos ? afterPos.signedQty : 0;

                    return jsonResponse({
                        success: true,
                        mode: "LIVE",
                        state: netAfter === 0 ? "EXIT_VERIFIED_NET_ZERO" : "EXIT_PARTIAL_REMAINING",
                        brokerOrderId: exitOrderId,
                        cancelledOrdersCount,
                        closedQty: closeQty,
                        netQtyAfter: netAfter,
                        netZeroVerified: netAfter === 0
                    }, 200, cors, correlationId);
                }
            }

            if (target === "reconcile_order") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const { broker, brokerOrderId, clientOrderId } = body;
                const vault = await safeReadVault(env, authUser.uid);

                let normalizedStatus = "UNKNOWN";
                let avgPrice = null;
                let filledQty = 0;
                let rejectReason = "";

                if (broker === "SHOONYA" && vault.shoonyaUid && vault.shoonyaToken) {
                    const chk = await verifyShoonyaOrderStatus(env, authUser.uid, vault, brokerOrderId, 1);
                    normalizedStatus = chk.status;
                    avgPrice = chk.avgPrice;
                    filledQty = chk.filledQty;
                    rejectReason = chk.rejectReason;
                } else if (broker === "COINDCX" && vault.coindcxKey && vault.coindcxSecret) {
                    const payloadStr = JSON.stringify({ timestamp: Date.now(), id: brokerOrderId, margin_currency_short_name: ["USDT", "INR"] });
                    const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);
                    const dRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": sig },
                        body: payloadStr
                    });
                    const dData = await dRes.json().catch(() => ({}));
                    const ord = Array.isArray(dData) ? dData.find(o => o.id === brokerOrderId) : dData;
                    if (ord && ord.status) {
                        const st = String(ord.status).toLowerCase();
                        if (st === "filled") normalizedStatus = "FILLED";
                        else if (st === "partially_filled") normalizedStatus = "PARTIALLY_FILLED";
                        else if (st === "open" || st === "initial" || st === "untriggered") normalizedStatus = "OPEN";
                        else if (st === "rejected") normalizedStatus = "REJECTED";
                        else if (st === "cancelled") normalizedStatus = "CANCELLED";
                        avgPrice = Number(ord.avg_price || ord.price || 0) || null;
                        filledQty = Number(ord.total_quantity || 0) - Number(ord.remaining_quantity || 0);
                    }
                }

                if (clientOrderId && env.RISK_GUARD) {
                    const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                    await doStub.fetch("https://guard/update-order-state", {
                        method: "POST",
                        body: JSON.stringify({ clientOrderId, state: normalizedStatus, brokerOrderId, avgPrice, filledQty })
                    });
                }

                return jsonResponse({ success: true, status: normalizedStatus, avgPrice, filledQty, rejectReason }, 200, cors, correlationId);
            }

            if (target === "news_feed") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`PUB_RL:${ip}`, 60, 60000)) return jsonResponse({ success: false, status: "RATE_LIMITED", items: [] }, 429, cors, correlationId);

                const newsCacheKey = new Request("https://edge-cache.araf/news_feed_live");
                try {
                    const cachedNews = await caches.default.match(newsCacheKey);
                    if (cachedNews) {
                        return new Response(cachedNews.body, {
                            status: cachedNews.status,
                            headers: { "Content-Type": "application/json", ...cors }
                        });
                    }
                } catch (e) {}

                const feeds = [
                    { url: "https://www.federalreserve.gov/feeds/press_monetary.xml", source: "Federal Reserve (Official)", trustTier: "OFFICIAL" },
                    { url: "https://news.google.com/rss/search?q=(site:rbi.org.in+OR+site:sebi.gov.in+OR+site:nseindia.com)+when:2d&hl=en-IN&gl=IN&ceid=IN:en", source: "RBI / SEBI / NSE Official", trustTier: "OFFICIAL" },
                    { url: "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", source: "Economic Times", trustTier: "TRUSTED_MEDIA" },
                    { url: "https://www.moneycontrol.com/rss/MCtopnews.xml", source: "Moneycontrol", trustTier: "TRUSTED_MEDIA" }
                ];

                const seenSlugs = new Set();
                const allItems = [];
                const firstSeenAt = Date.now();

                const classifyE01 = (title, baseTier) => {
                    const low = title.toLowerCase();
                    const isRumor = /(rumor|rumour|unconfirmed|buzz|speculation|sources say|likely to|may consider)/i.test(low);
                    const trustTier = isRumor ? "LOW_TRUST_RUMOR" : baseTier;

                    let eventCategory = "GENERAL_MARKET";
                    if (/(war|missile|strike|conflict|military|middle east|iran|israel|ukraine|russia)/i.test(low)) eventCategory = "WAR_GEOPOLITICS";
                    else if (/(rbi|fed|fomc|rate|repo|monetary|policy|interest|sebi)/i.test(low)) eventCategory = "CENTRAL_BANK_POLICY";
                    else if (/(tariff|sanction|embargo|trade war|ban)/i.test(low)) eventCategory = "SANCTIONS_TARIFF";
                    else if (/(halt|circuit|suspend|crash|disaster|emergency|outage)/i.test(low)) eventCategory = "CRITICAL_EVENT";
                    else if (/(earnings|profit|loss|merger|acquisition|ipo|dividend|results)/i.test(low)) eventCategory = "CORPORATE_EXCHANGE";
                    else if (/(inflation|cpi|gdp|jobs|payrolls|crude|oil)/i.test(low)) eventCategory = "MACRO_DATA";

                    const isBull = /(rally|surge|jump|cut rate|stimulus|approve|record high|beat|peace|ceasefire)/i.test(low);
                    const isBear = /(crash|plunge|halt|war|missile|hike|sanction|tariff|default|fraud|ban|miss)/i.test(low);
                    const impact = (isBull && !isBear) ? "BULLISH" : ((isBear && !isBull) ? "BEARISH" : "UNCERTAIN");

                    return { trustTier, eventCategory, impact };
                };

                await Promise.allSettled(feeds.map(async (f) => {
                    try {
                        const r = await fetch(f.url, { headers: { "User-Agent": "Mozilla/5.0", "Accept": "application/xml, text/xml" }, cf: { cacheTtl: 60 } });
                        if (!r.ok) return;
                        const xmlText = await r.text();
                        const itemBlocks = xmlText.match(/<item>[\s\S]*?<\/item>/gi) || [];
                        const atomBlocks = itemBlocks.length ? itemBlocks : (xmlText.match(/<entry>[\s\S]*?<\/entry>/gi) || []);
                        for (const block of atomBlocks.slice(0, 10)) {
                            const titleRaw = (block.match(/<title(?:[^>]*)>([\s\S]*?)<\/title>/i)?.[1] || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const linkRaw = (block.match(/<link[^>]*href=["']([^"']+)["'][^>]*>/i)?.[1] ||
                                block.match(/<link>([\s\S]*?)<\/link>/i)?.[1] || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const pubDate = (block.match(/<(?:pubDate|published|updated)>([\s\S]*?)<\/(?:pubDate|published|updated)>/i)?.[1] || "").trim();
                            const normalizedPubDate = pubDate.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const cleanTitle = decodeHtmlEntities(titleRaw.replace(/<[^>]+>/g, "")).trim();
                            if (!cleanTitle) continue;
                            const parsedTime = Date.parse(normalizedPubDate);
                            if (!Number.isFinite(parsedTime)) continue;
                            if (parsedTime < Date.now() - 72 * 60 * 60 * 1000) continue;

                            const slug = cleanTitle.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 48);
                            if (seenSlugs.has(slug)) continue;
                            seenSlugs.add(slug);

                            const meta = classifyE01(cleanTitle, f.trustTier);
                            allItems.push({
                                title: cleanTitle,
                                link: linkRaw.startsWith("http") ? linkRaw : "",
                                pubDate: normalizedPubDate || new Date().toUTCString(),
                                firstSeenAt,
                                verifiedSource: f.source,
                                trustTier: meta.trustTier,
                                eventCategory: meta.eventCategory,
                                impact: meta.impact
                            });
                        }
                    } catch (e) {}
                }));

                allItems.sort((a, b) => {
                    const aTime = Date.parse(a.pubDate);
                    const bTime = Date.parse(b.pubDate);
                    const safeATime = Number.isFinite(aTime) ? aTime : 0;
                    const safeBTime = Number.isFinite(bTime) ? bTime : 0;
                    return safeBTime - safeATime;
                });

                if (allItems.length > 0) {
                    const newsResponse = jsonResponse({ success: true, status: "LOADED", fetchedAt: firstSeenAt, items: allItems.slice(0, 30) }, 200, cors, correlationId);
                    await caches.default.put(newsCacheKey, new Response(newsResponse.clone().body, {
                        headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=60, max-age=60" }
                    })).catch(() => {});
                    return newsResponse;
                }
                return jsonResponse({ success: false, status: "UNAVAILABLE", items: [] }, 502, cors, correlationId);
            }

            if (target === "translate_text") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`TR_RL:${ip}`, 30, 60000)) return jsonResponse({ success: false, code: "RATE_LIMITED" }, 429, cors, correlationId);

                const { texts = [], tl = "bn" } = body;
                if (!Array.isArray(texts) || texts.length === 0 || tl === "en") {
                    return jsonResponse({ success: true, translations: Array.isArray(texts) ? texts : [] }, 200, cors, correlationId);
                }
                const allowedLangs = ["bn", "hi", "gu", "mr", "ta", "en"];
                const cleanTl = allowedLangs.includes(String(tl)) ? String(tl) : "bn";

                const results = await Promise.all(texts.slice(0, 15).map(async (t) => {
                    const cleanText = String(t || "").slice(0, 300);
                    try {
                        const lingvaUrl = `https://lingva.ml/api/v1/en/${encodeURIComponent(cleanTl)}/${encodeURIComponent(cleanText)}`;
                        const lr = await fetch(lingvaUrl, { cf: { cacheTtl: 300 } });
                        if (lr.ok) {
                            const lj = await lr.json().catch(() => null);
                            const translated = String(lj?.translation || "").trim();
                            if (translated && translated.toLowerCase() !== cleanText.toLowerCase()) return translated;
                        }
                    } catch (e) {}
                    try {
                        const r = await fetch(`https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=${encodeURIComponent(cleanTl)}&dt=t&q=${encodeURIComponent(cleanText)}`);
                        if (r.ok) {
                            const j = await r.json();
                            const translated = Array.isArray(j?.[0]) ? j[0].map(x => x[0]).join("") : "";
                            if (translated && translated.trim().toLowerCase() !== cleanText.toLowerCase()) return translated.trim();
                        }
                    } catch (e) {}
                    return cleanText;
                }));
                const serverTranslated = results.some((value, index) => value !== String(texts[index] || "").slice(0, 300));
                return jsonResponse({ success: true, serverTranslated, translations: results }, 200, cors, correlationId);
            }

            if (target === "ipo_feed") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`IPO_RL:${ip}`, 30, 60000)) return jsonResponse({ success: false, status: "RATE_LIMITED", items: [] }, 429, cors, correlationId);

                const ipoCacheKey = new Request("https://edge-cache.araf/ipo_feed");
                const ipoCached = await caches.default.match(ipoCacheKey);
                if (ipoCached) {
                    return new Response(ipoCached.body, {
                        status: ipoCached.status,
                        headers: { "Content-Type": "application/json", ...cors }
                    });
                }

                try {
                    let parsed = [];
                    const todayIst = getIstDateStr();

                    try {
                        const nseRes = await fetch("https://www.nseindia.com/api/ipo-current-issues", {
                            headers: {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                "Accept": "application/json,text/plain,*/*"
                            },
                            cf: { cacheTtl: 300 }
                        });
                        if (nseRes.ok) {
                            const nseJson = await nseRes.json().catch(() => null);
                            const nseRows = Array.isArray(nseJson) ? nseJson : (nseJson?.data || nseJson?.issues || []);
                            for (const row of nseRows) {
                                if (!row) continue;
                                const name = String(row.companyName || row.issueName || row.symbol || "").trim();
                                if (!name) continue;
                                const slug = name.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 32);
                                parsed.push({
                                    id: slug,
                                    name,
                                    type: String(row.issueType || row.series || "").toLowerCase().includes("sme") ? "sme" : "mainboard",
                                    gmp: "N/A",
                                    gmpNumeric: null,
                                    band: String(row.priceBand || row.issuePrice || "N/A"),
                                    sub: String(row.status || "N/A"),
                                    dates: String(row.issueDate || row.listingDate || "Check RHP"),
                                    size: String(row.issueSize || "NSE/BSE"),
                                    status: "Active",
                                    source: "NSE Public IPO API",
                                    lastUpdatedAt: Date.now(),
                                    gmpHistory: []
                                });
                            }
                        }
                    } catch (e) {}

                    if (parsed.length === 0) {
                        const r = await fetch("https://ipowatch.in/ipo-grey-market-premium-latest-gmp/", {
                            headers: { "User-Agent": "Mozilla/5.0" },
                            cf: { cacheTtl: 300 }
                        });
                        if (!r.ok) throw new Error("IPO source failed");
                        const html = await r.text();
                        const rows = html.match(/<tr[^>]*>[\s\S]*?<\/tr>/gi) || [];

                        for (const row of rows.slice(1, 25)) {
                            const cols = [...row.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map(m => decodeHtmlEntities(m[1].replace(/<[^>]+>/g, "")).trim());
                            if (cols.length >= 4 && cols[0]) {
                                const name = cols[0].replace(/\(.*\)/, "").trim();
                                const slug = name.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 32);
                                const gmpStr = cols[1].includes("₹") ? cols[1] : `₹${cols[1]}`;
                                const gmpNum = parseFloat(cols[1].replace(/[^0-9.-]/g, ""));
                                const histKey = `IPO_GMP_HIST:${slug}`;
                                let gmpHistory = [];
                                try {
                                    const hRaw = await env.ARAF_KV.get(histKey);
                                    gmpHistory = hRaw ? JSON.parse(hRaw) : [];
                                    if (Number.isFinite(gmpNum)) {
                                        const last = gmpHistory[gmpHistory.length - 1];
                                        if (!last || last.date !== todayIst || last.gmp !== gmpNum) {
                                            gmpHistory.push({ date: todayIst, gmp: gmpNum, ts: Date.now() });
                                            gmpHistory = gmpHistory.slice(-14);
                                        }
                                    }
                                } catch (e) {}

                                parsed.push({
                                    id: slug,
                                    name,
                                    type: name.toLowerCase().includes("sme") ? "sme" : "mainboard",
                                    gmp: gmpStr,
                                    gmpNumeric: Number.isFinite(gmpNum) ? gmpNum : null,
                                    band: cols[2] || "N/A",
                                    sub: cols[3] || "N/A",
                                    dates: cols[4] || "Check RHP",
                                    size: cols[5] || "NSE/BSE",
                                    status: "Active",
                                    source: "IPOWatch (Indicative Grey Market)",
                                    lastUpdatedAt: Date.now(),
                                    gmpHistory
                                });
                            }
                        }
                    }
                    if (parsed.length > 0) {
                        const ipoResponse = jsonResponse({
                            success: true,
                            status: "LOADED",
                            source: parsed.every(item => item.source === "NSE Public IPO API") ? "NSE Public IPO API" : "IPOWatch (Indicative Grey Market)",
                            disclaimer: "GMP is grey-market indicative only, NOT official listing price or guaranteed profit.",
                            fetchedAt: Date.now(),
                            items: parsed
                        }, 200, cors, correlationId);
                        await caches.default.put(ipoCacheKey, new Response(ipoResponse.clone().body, {
                            headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=300, max-age=300" }
                        })).catch(() => {});
                        return ipoResponse;
                    }
                } catch (e) {}
                return jsonResponse({ success: false, status: "UNAVAILABLE", items: [] }, 502, cors, correlationId);
            }

            if (target === "ipo_news_feed") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const tl = String(url.searchParams.get("tl") || "bn").trim();
                const qFilter = String(url.searchParams.get("q") || "").trim();
                const allowedLangs = ["bn", "hi", "gu", "mr", "ta", "en"];
                const cleanTl = allowedLangs.includes(tl) ? tl : "bn";

                const ipoQuery = qFilter ? `${qFilter} IPO India` : "(IPO GMP OR DRHP OR RHP OR SEBI IPO OR NSE SME IPO)";
                const feeds = [
                    { url: `https://news.google.com/rss/search?q=${encodeURIComponent(ipoQuery + " (site:sebi.gov.in OR site:nseindia.com OR site:bseindia.com)")}&hl=en-IN&gl=IN&ceid=IN:en`, source: "SEBI / NSE / BSE Filing", trustTier: "OFFICIAL" },
                    { url: `https://news.google.com/rss/search?q=${encodeURIComponent(ipoQuery + " when:7d")}&hl=en-IN&gl=IN&ceid=IN:en`, source: "IPO Catalyst Wire", trustTier: "TRUSTED_MEDIA" },
                    { url: "https://economictimes.indiatimes.com/markets/ipos/fpos/rssfeeds/15097148.cms", source: "Economic Times IPO", trustTier: "TRUSTED_MEDIA" }
                ];

                const seenSlugs = new Set();
                const items = [];
                await Promise.allSettled(feeds.map(async (f) => {
                    try {
                        const r = await fetch(f.url, { headers: { "User-Agent": "Mozilla/5.0" }, cf: { cacheTtl: 120 } });
                        if (!r.ok) return;
                        const xml = await r.text();
                        const blocks = xml.match(/<item>[\s\S]*?<\/item>/gi) || [];
                        for (const block of blocks.slice(0, 10)) {
                            const titleRaw = (block.match(/<title>([\s\S]*?)<\/title>/i)?.[1] || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const linkRaw = (block.match(/<link>([\s\S]*?)<\/link>/i)?.[1] || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const pubDate = (block.match(/<pubDate>([\s\S]*?)<\/pubDate>/i)?.[1] || "").trim();
                            const normalizedPubDate = pubDate.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").trim();
                            const cleanTitle = decodeHtmlEntities(titleRaw.replace(/<[^>]+>/g, "")).trim();
                            if (!cleanTitle) continue;
                            if (normalizedPubDate && !Number.isFinite(Date.parse(normalizedPubDate))) continue;
                            const slug = cleanTitle.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 46);
                            if (seenSlugs.has(slug)) continue;
                            seenSlugs.add(slug);

                            const isRumor = /(buzz|rumor|rumour|likely|sources|speculation|grey market)/i.test(cleanTitle);
                            items.push({
                                title: cleanTitle,
                                link: linkRaw.startsWith("http") ? linkRaw : "",
                                pubDate: normalizedPubDate || new Date().toUTCString(),
                                verifiedSource: f.source,
                                trustTier: isRumor ? "LOW_TRUST_RUMOR" : f.trustTier
                            });
                        }
                    } catch (e) {}
                }));

                items.sort((a, b) => (a.trustTier === "OFFICIAL" ? -1 : 1) - (b.trustTier === "OFFICIAL" ? -1 : 1) || (Date.parse(b.pubDate) || 0) - (Date.parse(a.pubDate) || 0));
                const topItems = items.slice(0, 15);
                if (cleanTl !== "en" && topItems.length > 0) {
                    const trList = await translateTextArray(topItems.map(x => x.title), cleanTl, env);
                    topItems.forEach((it, idx) => { it.translatedTitle = trList[idx] || it.title; });
                }
                return jsonResponse({ success: topItems.length > 0, lang: cleanTl, fetchedAt: Date.now(), items: topItems }, 200, cors, correlationId);
            }

            if (target === "deep_scanner") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const ip = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`SCAN_RL:${ip}`, 45, 60000)) return jsonResponse({ success: false, code: "RATE_LIMITED", items: [] }, 429, cors, correlationId);

                const exchangeFilter = String(url.searchParams.get("exchange") || "ALL").toUpperCase();
                const eligibleOnly = url.searchParams.get("eligibleOnly") === "1";
                const page = Math.max(1, parseInt(url.searchParams.get("page") || "1", 10) || 1);
                const pageSize = Math.min(500, Math.max(20, parseInt(url.searchParams.get("pageSize") || "250", 10) || 250));
                const cacheKey = "UNIVERSE_SCAN_CACHE:INDIA";
                const scannerCacheKey = new Request("https://edge-cache.araf/universe_scan_india");

                let snapshot = null;
                try {
                    const cachedResponse = await caches.default.match(scannerCacheKey);
                    if (cachedResponse) snapshot = await cachedResponse.json();
                } catch (e) {}

                if (!snapshot || !Array.isArray(snapshot.items) || (Date.now() - Number(snapshot.asOf || 0) > 60000)) {
                    const chunkSize = 1800;
                    const maxChunks = 6;
                    let providerTotalCount = null;
                    let scanAttempted = 0;
                    let failed = 0;
                    const allItems = [];
                    const seenKeys = new Set();

                    for (let c = 0; c < maxChunks; c++) {
                        const start = c * chunkSize;
                        const end = start + chunkSize;
                        const scanReq = {
                            filter: [{ left: "exchange", operation: "in_range", right: ["NSE", "BSE"] }],
                            options: { lang: "en" },
                            markets: ["india"],
                            symbols: { query: { types: ["stock"] }, tickers: [] },
                            columns: [
                                "name", "description", "exchange", "close", "change", "volume",
                                "relative_volume_10d_calc", "ATR", "RSI", "EMA20", "EMA50",
                                "sector", "high", "low", "open", "Recommend.All"
                            ],
                            sort: { sortBy: "volume", sortOrder: "desc" },
                            range: [start, end]
                        };

                        try {
                            const tvRes = await fetch("https://scanner.tradingview.com/india/scan", {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/json",
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                                },
                                body: JSON.stringify(scanReq)
                            });
                            if (!tvRes.ok) { failed += chunkSize; break; }
                            const tvJson = await tvRes.json().catch(() => null);
                            if (providerTotalCount === null && Number.isFinite(Number(tvJson?.totalCount))) {
                                providerTotalCount = Number(tvJson.totalCount);
                            }
                            const rows = Array.isArray(tvJson?.data) ? tvJson.data : [];
                            scanAttempted += rows.length;
                            if (rows.length === 0) break;

                            for (const r of rows) {
                                const d = r.d || [];
                                const tickerRaw = String(r.s || "");
                                const exch = String(d[2] || (tickerRaw.startsWith("BSE:") ? "BSE" : "NSE")).toUpperCase();
                                const sym = String(d[0] || tickerRaw.split(":")[1] || "").toUpperCase();
                                const uniqueId = `${exch}:${sym}`;
                                if (!sym || seenKeys.has(uniqueId)) continue;
                                seenKeys.add(uniqueId);

                                const price = Number(d[3]);
                                const vol = Number(d[5]);
                                const validPrice = Number.isFinite(price) && price > 0;
                                const validVol = Number.isFinite(vol) && vol >= 0;
                                const chgPct = Number.isFinite(Number(d[4])) ? Number(d[4]) : null;
                                const rvolVal = (d[6] !== null && d[6] !== undefined && Number.isFinite(Number(d[6]))) ? Number(Number(d[6]).toFixed(2)) : null;
                                const atrVal = (d[7] !== null && d[7] !== undefined && Number.isFinite(Number(d[7]))) ? Number(Number(d[7]).toFixed(2)) : null;
                                const rsiVal = (d[8] !== null && d[8] !== undefined && Number.isFinite(Number(d[8]))) ? Number(Number(d[8]).toFixed(1)) : null;

                                let eligible = true;
                                let skipReason = null;
                                if (!validPrice) { eligible = false; skipReason = "INVALID_PRICE"; }
                                else if (!validVol || vol < 25000) { eligible = false; skipReason = "LOW_LIQUIDITY"; }
                                else if (price < 10) { eligible = false; skipReason = "BELOW_MIN_PRICE"; }

                                if (validPrice) {
                                    allItems.push({
                                        id: `scan_${exch}_${sym}`.toLowerCase().replace(/[^a-z0-9_]/g, ""),
                                        orderKey: sym,
                                        symbol: uniqueId,
                                        yahooSymbol: `${sym}.${exch === "BSE" ? "BO" : "NS"}`,
                                        name: String(d[1] || sym).slice(0, 48),
                                        exchange: exch,
                                        instrumentType: "EQUITY",
                                        category: "indian",
                                        price,
                                        open: Number.isFinite(Number(d[14])) ? Number(d[14]) : price,
                                        high: Number.isFinite(Number(d[12])) ? Number(d[12]) : price,
                                        low: Number.isFinite(Number(d[13])) ? Number(d[13]) : price,
                                        changePct: chgPct !== null ? Number(chgPct.toFixed(2)) : 0,
                                        change: chgPct !== null ? `${chgPct >= 0 ? "+" : ""}${chgPct.toFixed(2)}%` : "0.00%",
                                        volume: validVol ? vol : null,
                                        turnoverInr: validVol ? Number((price * vol).toFixed(0)) : 0,
                                        rvol: rvolVal,
                                        atr: atrVal,
                                        rsi: rsiVal,
                                        ema20: Number.isFinite(Number(d[9])) ? Number(Number(d[9]).toFixed(2)) : null,
                                        ema50: Number.isFinite(Number(d[10])) ? Number(Number(d[10]).toFixed(2)) : null,
                                        sector: String(d[11] || "Unclassified"),
                                        tvRec: Number.isFinite(Number(d[15])) ? Number(Number(d[15]).toFixed(2)) : null,
                                        eligible,
                                        skipReason,
                                        dataQuality: "DELAYED"
                                    });
                                } else {
                                    failed++;
                                }
                            }
                            if (rows.length < chunkSize || (providerTotalCount !== null && end >= providerTotalCount)) break;
                        } catch (e) {
                            failed++;
                            break;
                        }
                    }

                    if (allItems.length > 0) {
                        const nseCount = allItems.filter(x => x.exchange === "NSE").length;
                        const bseCount = allItems.filter(x => x.exchange === "BSE").length;
                        const eligibleCount = allItems.filter(x => x.eligible).length;
                        const skippedCount = allItems.length - eligibleCount;
                        const expectedUniverse = providerTotalCount !== null ? providerTotalCount : allItems.length;
                        const coverageGap = Math.max(0, expectedUniverse - allItems.length);

                        snapshot = {
                            source: "TRADINGVIEW_INDIA_PAGINATED",
                            asOf: Date.now(),
                            expectedUniverse,
                            discovered: allItems.length,
                            scanAttempted,
                            baselineScanned: allItems.length,
                            eligibleCount,
                            skippedCount,
                            failedCount: failed,
                            coverageGap,
                            nseCount,
                            bseCount,
                            items: allItems
                        };
                        await caches.default.put(scannerCacheKey, new Response(JSON.stringify(snapshot), {
                            headers: { "Content-Type": "application/json", "Cache-Control": "s-maxage=60, max-age=60" }
                        })).catch(() => {});
                    }
                }

                if (!snapshot || !snapshot.items.length) {
                    return jsonResponse({ success: false, source: "SCANNER_UNAVAILABLE", items: [] }, 200, cors, correlationId);
                }

                let viewItems = snapshot.items;
                if (exchangeFilter === "NSE" || exchangeFilter === "BSE") {
                    viewItems = viewItems.filter(x => x.exchange === exchangeFilter);
                }
                if (eligibleOnly) {
                    viewItems = viewItems.filter(x => x.eligible);
                }

                const totalFiltered = viewItems.length;
                const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize));
                const safePage = Math.min(page, totalPages);
                const startIdx = (safePage - 1) * pageSize;
                const pageItems = viewItems.slice(startIdx, startIdx + pageSize);

                return jsonResponse({
                    success: true,
                    source: snapshot.source,
                    asOf: snapshot.asOf,
                    fetchedAt: snapshot.asOf,
                    expectedUniverse: snapshot.expectedUniverse,
                    discovered: snapshot.discovered,
                    scanAttempted: snapshot.scanAttempted,
                    baselineScanned: snapshot.baselineScanned,
                    eligibleCount: snapshot.eligibleCount,
                    skippedCount: snapshot.skippedCount,
                    failedCount: snapshot.failedCount,
                    coverageGap: snapshot.coverageGap,
                    nseCount: snapshot.nseCount,
                    bseCount: snapshot.bseCount,
                    page: safePage,
                    pageSize,
                    totalPages,
                    totalFiltered,
                    returnedCount: pageItems.length,
                    items: pageItems
                }, 200, cors, correlationId);
            }

            if (target === "news_article") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const articleUrl = String(url.searchParams.get("url") || "").trim();
                const tl = String(url.searchParams.get("tl") || "en").trim();
                const fallbackTitle = String(url.searchParams.get("title") || "").trim();

                if (!articleUrl || isPrivateOrUnsafeUrl(articleUrl)) {
                    return jsonResponse({ success: false, error: "Unsafe or missing article URL" }, 400, cors, correlationId);
                }

                try {
                    const r = await fetch(articleUrl, {
                        headers: {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                            "Accept": "text/html,application/xhtml+xml"
                        }
                    });
                    const html = r.ok ? await r.text() : "";
                    const cleaned = html
                        .replace(/<script[\s\S]*?<\/script>/gi, " ")
                        .replace(/<style[\s\S]*?<\/style>/gi, " ")
                        .replace(/<nav[\s\S]*?<\/nav>/gi, " ")
                        .replace(/<footer[\s\S]*?<\/footer>/gi, " ");

                    const titleM = cleaned.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i) || cleaned.match(/<title>([\s\S]*?)<\/title>/i);
                    let rawTitle = decodeHtmlEntities((titleM?.[1] || fallbackTitle || "Market Catalyst").replace(/<[^>]+>/g, "").trim());

                    const pMatches = [...cleaned.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/gi)]
                        .map(m => decodeHtmlEntities(m[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()))
                        .filter(txt => txt.length >= 45 && !/cookie|subscribe|copyright|sign in|advertisement/i.test(txt))
                        .slice(0, 12);

                    let paragraphs = pMatches.length > 0 ? pMatches : [fallbackTitle || "Full story snippet unavailable from publisher."];
                    if (tl && tl !== "en") {
                        const [trTitle, ...trParas] = await translateTextArray([rawTitle, ...paragraphs], tl, env);
                        rawTitle = trTitle || rawTitle;
                        paragraphs = trParas.map((p, idx) => p || paragraphs[idx]);
                    }
                    return jsonResponse({ success: true, title: rawTitle, paragraphs, lang: tl }, 200, cors, correlationId);
                } catch (e) {
                    return jsonResponse({ success: false, title: fallbackTitle, paragraphs: [fallbackTitle || "Could not load full article text."], lang: "en" }, 200, cors, correlationId);
                }
            }

            if (target === "broker_holdings") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);
                const shoonya = [];
                const coindcx = [];

                await Promise.allSettled([
                    (async () => {
                        if (!vault.shoonyaUid || !vault.shoonyaToken) return;
                        const r = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/Holdings", {
                            method: "POST",
                            headers: { "Content-Type": "application/x-www-form-urlencoded" },
                            body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid, actid: vault.shoonyaUid, prd: "C" }))}&jKey=${vault.shoonyaToken}`
                        });
                        const list = await r.json().catch(() => []);
                        if (Array.isArray(list)) {
                            list.forEach(h => {
                                const exchItem = Array.isArray(h.exch_tsym) ? h.exch_tsym[0] : {};
                                const qty = Number(h.holdqty || 0) + Number(h.btstqty || 0) + Number(h.npoadqty || 0);
                                const avg = Number(h.upldprc || 0);
                                const ltp = Number(exchItem?.ltp || avg || 0);
                                if (qty > 0) {
                                    const inv = qty * avg;
                                    const cur = qty * ltp;
                                    shoonya.push({
                                        symbol: exchItem?.tsym || h.tsym || "HOLDING",
                                        qty, avgPrice: avg.toFixed(2), ltp,
                                        investedVal: inv, currentVal: cur, totalReturnVal: cur - inv
                                    });
                                }
                            });
                        }
                    })(),
                    (async () => {
                        if (!vault.coindcxKey || !vault.coindcxSecret) return;
                        const payloadStr = JSON.stringify({ timestamp: Date.now() });
                        const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);
                        const [balRes, tickRes] = await Promise.all([
                            brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/users/balances", {
                                method: "POST",
                                headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": sig },
                                body: payloadStr
                            }),
                            fetch("https://api.coindcx.com/exchange/ticker").catch(() => null)
                        ]);
                        const list = await balRes.json().catch(() => []);
                        const ticks = tickRes ? await tickRes.json().catch(() => []) : [];
                        if (Array.isArray(list)) {
                            list.filter(b => Number(b.balance || 0) > 0 && b.currency !== "INR").forEach(b => {
                                const qty = Number(b.balance || 0);
                                const tk = Array.isArray(ticks) ? ticks.find(t => t.market === `${b.currency}INR`) : null;
                                const ltpInr = Number(tk?.last_price || (b.currency === "USDT" ? 86 : 0));
                                const cur = qty * ltpInr;
                                if (cur > 1) {
                                    coindcx.push({
                                        symbol: b.currency, qty: Number(qty.toFixed(4)),
                                        avgPrice: ltpInr.toFixed(2), ltp: ltpInr,
                                        investedVal: cur, currentVal: cur, totalReturnVal: 0
                                    });
                                }
                            });
                        }
                    })()
                ]);

                return jsonResponse({ success: true, shoonya, coindcx }, 200, cors, correlationId);
            }

            if (target === "broker_orders") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);
                const pending = [];
                await Promise.allSettled([
                    (async () => {
                        if (!vault.shoonyaUid || !vault.shoonyaToken) return;
                        const r = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/OrderBook", {
                            method: "POST",
                            headers: { "Content-Type": "application/x-www-form-urlencoded" },
                            body: `jData=${encodeURIComponent(JSON.stringify({ uid: vault.shoonyaUid }))}&jKey=${vault.shoonyaToken}`
                        });
                        const list = await r.json().catch(() => []);
                        if (Array.isArray(list)) {
                            list.filter(o => ["OPEN", "PENDING", "TRIGGER_PENDING"].includes(String(o.status || "").toUpperCase())).forEach(o => {
                                pending.push({
                                    broker: "SHOONYA", orderId: o.norenordno, symbol: o.tsym,
                                    side: o.trantype === "B" ? "BUY" : "SELL", qty: Number(o.qty || 0),
                                    price: Number(o.prc || o.trgprc || 0), status: o.status
                                });
                            });
                        }
                    })(),
                    (async () => {
                        if (!vault.coindcxKey || !vault.coindcxSecret) return;
                        const payloadStr = JSON.stringify({ timestamp: Date.now(), status: "open", page: "1", size: "30", margin_currency_short_name: ["USDT", "INR"] });
                        const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);
                        const r = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders", {
                            method: "POST",
                            headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": sig },
                            body: payloadStr
                        });
                        const list = await r.json().catch(() => []);
                        if (Array.isArray(list)) {
                            list.forEach(o => {
                                pending.push({
                                    broker: "COINDCX", orderId: o.id, symbol: o.pair,
                                    side: String(o.side || "").toUpperCase(), qty: Number(o.total_quantity || 0),
                                    price: Number(o.price || o.stop_price || 0), status: o.status
                                });
                            });
                        }
                    })()
                ]);
                return jsonResponse({ success: true, pending }, 200, cors, correlationId);
            }
            if (target === "ai_guardian") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const { query, langName = "English", context = {}, history = [] } = body;
                if (!query) return jsonResponse({ error: "Empty query" }, 400, cors, correlationId);

                const systemPrompt = `You are ARAF INTELLIGENCE GUARDIAN, an institutional trading analyst.
RULES:
1. Reply strictly in friendly, simple ${langName}.
2. ONLY answer Stock Market (NSE, BSE, Global Equities, F&O, IPOs, Macro) and Cryptocurrency Market questions. Politely refuse off-topic requests.
3. Never invent fake prices or guaranteed win rates. If Quality is OFFLINE or STALE, warn the user.`;

                const ctxText = `Asset=${context.asset || "UNKNOWN"}, LTP=${context.ltp || "N/A"}, Quality=${context.feedQuality || "OFFLINE"}, Regime=${context.regime || "UNKNOWN"}, TwoWaySignal=${context.twoWaySignal || "NO TRADE"}, EMA9=${context.ema9 || "N/A"}, EMA21=${context.ema21 || "N/A"}, RSI14=${context.rsi14 || "N/A"}, ATR=${context.atr || "N/A"}, RealizedVol=${context.realizedVol || "N/A"}%, VWAP=${context.vwap || "N/A"}, S1=${context.s1 || "N/A"}, R1=${context.r1 || "N/A"}, VIX=${context.vix || "UNAVAILABLE"}, LatestCatalysts=${context.latestCatalysts || "No recent catalyst"}`;
                const userPrompt = `Market Context: [${ctxText}]\nTrader Question: ${String(query).slice(0, 600)}`;

                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 14000);

                try {
                    // Tier 1: Gemini routing with history and thinking disabled
                    const geminiKey = env.GEMINI_API_KEY || "";
                    if (geminiKey) {
                        const recentHistory = Array.isArray(history)
                            ? history.slice(-4).map(m => ({
                                role: m.role === "assistant" ? "model" : "user",
                                parts: [{ text: String(m.content || "").slice(0, 900) }]
                            })).filter(m => m.parts[0].text)
                            : [];
                        const geminiContents = [...recentHistory, { role: "user", parts: [{ text: userPrompt }] }];

                        for (const model of ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]) {
                            const isVertexExpress = geminiKey.startsWith("AQ.");
                            const gUrl = isVertexExpress
                                ? "https://aiplatform.googleapis.com/v1/publishers/google/models/" + model + ":generateContent"
                                : `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${encodeURIComponent(geminiKey)}`;
                            const gRes = await fetch(gUrl, {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/json",
                                    ...(isVertexExpress ? { "Authorization": `Bearer ${geminiKey}` } : {})
                                },
                                signal: controller.signal,
                                body: JSON.stringify({
                                    systemInstruction: { parts: [{ text: systemPrompt }] },
                                    contents: geminiContents,
                                    generationConfig: { temperature: 0.35, maxOutputTokens: 700, thinkingBudget: 0 }
                                })
                            }).catch(() => null);
                            if (gRes && gRes.ok) {
                                const gj = await gRes.json().catch(() => null);
                                const gText = gj?.candidates?.[0]?.content?.parts?.[0]?.text;
                                if (gText && gText.trim()) return jsonResponse({ success: true, source: `GEMINI_${model.toUpperCase()}`, reply: gText.trim() }, 200, cors, correlationId);
                            }
                        }
                    }

                    // Tier 2: Zero-Key OpenAI-Compatible Backend Proxy
                    const oaiRes = await fetch("https://text.pollinations.ai/openai", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        signal: controller.signal,
                        body: JSON.stringify({
                            model: "openai",
                            messages: [
                                { role: "system", content: systemPrompt },
                                { role: "user", content: userPrompt }
                            ]
                        })
                    }).catch(() => null);
                    if (oaiRes && oaiRes.ok) {
                        const oaiJson = await oaiRes.json().catch(() => null);
                        const oaiText = oaiJson?.choices?.[0]?.message?.content;
                        if (oaiText && oaiText.trim()) return jsonResponse({ success: true, source: "BACKEND_AI_ROUTER", reply: oaiText.trim() }, 200, cors, correlationId);
                    }

                    // Tier 3: Direct Pollinations Fallback
                    const pRes = await fetch("https://text.pollinations.ai/", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        signal: controller.signal,
                        body: JSON.stringify({
                            messages: [
                                { role: "system", content: systemPrompt },
                                { role: "user", content: userPrompt }
                            ]
                        })
                    }).catch(() => null);
                    const pReply = pRes && pRes.ok ? await pRes.text().catch(() => "") : "";
                    if (pReply && pReply.trim()) {
                        return jsonResponse({ success: true, source: "LIVE_AI_PROXY", reply: pReply.trim() }, 200, cors, correlationId);
                    }
                } catch (e) {} finally {
                    clearTimeout(timer);
                }

                // Tier 4: Intent-aware local quant synthesis
                const qLow = String(query).toLowerCase();
                let intentReply;
                if (/(buy|sell|trade|entry|order|target|stop loss|sl|position)/i.test(qLow)) {
                    intentReply = `Trade view for ${context.asset || "the selected asset"}: signal=${context.twoWaySignal || "WAIT"}, LTP=${context.ltp || "--"}, regime=${context.regime || "UNKNOWN"}.\nSupport S1=${context.s1 || "--"} and resistance R1=${context.r1 || "--"} should guide entry and invalidation. Protect capital with a defined stop-loss; do not chase extended candles.`;
                } else if (/(rsi|ema|trend|momentum|signal|technical|chart)/i.test(qLow)) {
                    intentReply = `Technical snapshot: ${context.asset || "Selected asset"} is ${context.regime || "UNKNOWN"} with EMA9=${context.ema9 || "--"}, EMA21=${context.ema21 || "--"}, RSI14=${context.rsi14 || "--"} and VWAP=${context.vwap || "--"}.\nThe current desk signal is ${context.twoWaySignal || "WAIT"}; confirm the direction with S1=${context.s1 || "--"} and R1=${context.r1 || "--"}.`;
                } else if (/(support|resistance|price|target|high|low)/i.test(qLow)) {
                    intentReply = `For ${context.asset || "the selected asset"}, LTP=${context.ltp || "--"}, S1=${context.s1 || "--"}, R1=${context.r1 || "--"} and the active regime is ${context.regime || "UNKNOWN"}.\nUse S1/R1 as structural zones, not guaranteed reversal levels.`;
                } else if (/(risk|capital|loss|drawdown|position size|lot)/i.test(qLow)) {
                    intentReply = `Risk guard view: ${context.asset || "The market"} is currently ${context.feedQuality || "OFFLINE"} with ${context.twoWaySignal || "WAIT"} signal.\nSize positions from a fixed invalidation point, protect downside with SL, and avoid adding risk after repeated losses.`;
                } else if (/(news|ipo|market|macro|economy)/i.test(qLow)) {
                    intentReply = `Market view: ${context.asset || "The market"} is ${context.feedQuality || "OFFLINE"}; current regime=${context.regime || "UNKNOWN"}, signal=${context.twoWaySignal || "WAIT"}.\nVerify official exchange, RBI/SEBI, policy and company filings before acting. Treat GMP and rumor-based headlines as unverified.`;
                } else {
                    intentReply = `ARAF desk snapshot for ${context.asset || "the market"}: LTP=${context.ltp || "--"}, quality=${context.feedQuality || "OFFLINE"}, regime=${context.regime || "UNKNOWN"}, signal=${context.twoWaySignal || "WAIT"}.\nCurrent structural levels are S1=${context.s1 || "--"} and R1=${context.r1 || "--"}. Risk management and live feed confirmation remain mandatory.`;
                }
                return jsonResponse({ success: true, source: "INTENT_AWARE_QUANT_FALLBACK", reply: intentReply }, 200, cors, correlationId);
            }

            if (target === "admin_login") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const reqIp = request.headers.get("CF-Connecting-IP") || "unknown";
                if (checkMemoryRateLimit(`ADMIN_RL:${reqIp}`, 10, 60000)) return jsonResponse({ success: false, code: "RATE_LIMITED", message: "Too many admin login attempts!" }, 429, cors, correlationId);

                const secret = String(body.adminSecret || "").trim();
                let isMatch = false;

                const customPassRaw = await env.ARAF_KV.get("ADMIN_CUSTOM_PASSWORD_HASH");
                if (customPassRaw) {
                    const customObj = JSON.parse(customPassRaw);
                    const derived = await derivePbkdf2PinHash(secret, customObj.saltHex);
                    isMatch = timingSafeEqual(derived, customObj.hash);
                }

               if (!customPassRaw && !env.ADMIN_PASSPHRASE) {
                    return jsonResponse({ success: false, message: "Admin authentication unavailable (Secret missing)" }, 503, cors, correlationId);
                }

                if (!isMatch && env.ADMIN_PASSPHRASE && secret) {
                    isMatch = timingSafeEqual(secret, env.ADMIN_PASSPHRASE);
                }
                if (!isMatch) {
                    auditLog("WARN", correlationId, "ADMIN_LOGIN_FAIL", { ip: reqIp });
                    return jsonResponse({ success: false, message: "Invalid Master Admin Secret!" }, 403, cors, correlationId);
                }

                const adminToken = await signStatelessToken(env, { type: "ADMIN", actor: "Founder Desk" }, 14400);
                auditLog("INFO", correlationId, "ADMIN_LOGIN_SUCCESS", { ip: reqIp });
                return jsonResponse({ success: true, adminToken }, 200, cors, correlationId);
            }

            if (target === "admin_forgot_password") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const targetEmail = "timeloreai@gmail.com";
                const cdKey = `ADMIN_OTP_CD`;
                if (await env.ARAF_KV.get(cdKey)) {
                    return jsonResponse({ success: false, message: "Please wait 60s before requesting another emergency OTP." }, 429, cors, correlationId);
                }

                if (!env.GMAIL_BRIDGE_URL || !env.GMAIL_BRIDGE_SECRET) {
                    return jsonResponse({ success: false, message: "Gmail Relay Bridge unconfigured" }, 503, cors, correlationId);
                }

                const rand = new Uint32Array(1);
                crypto.getRandomValues(rand);
                const otp = String((rand[0] % 900000) + 100000);
                const hash = await sha256Hash(`ADMIN_RESET:${targetEmail}:${otp}`);

                await Promise.all([
                    env.ARAF_KV.put("ADMIN_RESET_OTP", JSON.stringify({ hash, attempts: 0 }), { expirationTtl: 600 }),
                    env.ARAF_KV.put(cdKey, "1", { expirationTtl: 60 })
                ]);

                const bRes = await fetch(env.GMAIL_BRIDGE_URL, {
                    method: "POST",
                    redirect: "follow",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ secret: env.GMAIL_BRIDGE_SECRET, to: targetEmail, otp })
                }).catch(err => ({ ok: false, status: 502, error: err.message }));

                const rawResp = bRes && typeof bRes.text === "function" ? await bRes.text().catch(() => "") : "";
                let bData = {};
                try { bData = JSON.parse(rawResp); } catch (e) {}

                if (!bRes.ok || bData.success !== true) {
                    await Promise.allSettled([
                        env.ARAF_KV.delete("ADMIN_RESET_OTP"),
                        env.ARAF_KV.delete(cdKey)
                    ]);
                    const reason = bData.error || (rawResp.includes("accounts.google.com") ? "Apps Script access must be Anyone" : "Failed to dispatch recovery OTP. Rolled back cleanly.");
                    auditLog("WARN", correlationId, "ADMIN_OTP_DISPATCH_FAIL_ROLLBACK", { email: targetEmail, reason });
                    return jsonResponse({ success: false, message: reason }, 502, cors, correlationId);
                }

                auditLog("INFO", correlationId, "ADMIN_OTP_DISPATCHED", { email: targetEmail });
                return jsonResponse({ success: true, message: "OTP sent" }, 200, cors, correlationId);
            }

            if (target === "admin_verify_otp") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const otp = String(body.otp || "").trim();
                const raw = await env.ARAF_KV.get("ADMIN_RESET_OTP");
                if (!raw) return jsonResponse({ success: false, message: "Recovery OTP expired or not found" }, 400, cors, correlationId);

                const rec = JSON.parse(raw);
                if (rec.attempts >= 5) {
                    await env.ARAF_KV.delete("ADMIN_RESET_OTP");
                    return jsonResponse({ success: false, message: "Too many wrong attempts! OTP locked." }, 429, cors, correlationId);
                }

                const checkHash = await sha256Hash(`ADMIN_RESET:timeloreai@gmail.com:${otp}`);
                if (!timingSafeEqual(rec.hash, checkHash)) {
                    rec.attempts += 1;
                    await env.ARAF_KV.put("ADMIN_RESET_OTP", JSON.stringify(rec), { expirationTtl: 600 });
                    return jsonResponse({ success: false, message: "Invalid OTP code!" }, 401, cors, correlationId);
                }

                await env.ARAF_KV.delete("ADMIN_RESET_OTP");
                const otpToken = generateSecureToken();
                await env.ARAF_KV.put(`ADMIN_RESET_TOKEN:${otpToken}`, "1", { expirationTtl: 900 });

                return jsonResponse({ success: true, otpToken }, 200, cors, correlationId);
            }

            if (target === "admin_set_password") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const { newPassword, otpToken } = body;
                if (!otpToken || !(await env.ARAF_KV.get(`ADMIN_RESET_TOKEN:${otpToken}`))) {
                    return jsonResponse({ success: false, message: "Unauthorized password reset session" }, 403, cors, correlationId);
                }

                const pass = String(newPassword || "").trim();
                if (pass.length < 8) {
                    return jsonResponse({ success: false, message: "Password must be at least 8 characters!" }, 400, cors, correlationId);
                }

                const saltArr = new Uint8Array(16);
                crypto.getRandomValues(saltArr);
                const saltHex = Array.from(saltArr).map(b => b.toString(16).padStart(2, "0")).join("");
                const hash = await derivePbkdf2PinHash(pass, saltHex);

                await Promise.all([
                    env.ARAF_KV.put("ADMIN_CUSTOM_PASSWORD_HASH", JSON.stringify({ saltHex, hash, updatedAt: Date.now() })),
                    env.ARAF_KV.delete(`ADMIN_RESET_TOKEN:${otpToken}`)
                ]);

                const adminToken = await signStatelessToken(env, { type: "ADMIN", actor: "Founder Desk" }, 14400);

                auditLog("INFO", correlationId, "ADMIN_PASSWORD_UPDATED", { actor: "timeloreai" });
                return jsonResponse({ success: true, adminToken }, 200, cors, correlationId);
            }

            if (target === "admin_list_kyc") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const adm = await validateAdminAccess(request, env);
                if (!adm.ok) return jsonResponse({ error: "Forbidden: Admin authorization required" }, 403, cors, correlationId);

                const kycKeys = [];
                let kycCursor = undefined;
                do {
                    const page = await env.ARAF_KV.list({
                        prefix: "KYC:",
                        limit: 100,
                        ...(kycCursor ? { cursor: kycCursor } : {})
                    });
                    kycKeys.push(...page.keys);
                    kycCursor = page.list_complete ? undefined : page.cursor;
                } while (kycCursor);

                const applications = await Promise.all(kycKeys.map(async (k) => {
                    const raw = await env.ARAF_KV.get(k.name);
                    if (!raw) return null;
                    const dec = await decryptKycProfile(env, JSON.parse(raw));
                    if (!dec) return null;
                    const uid = dec.ownerUid;

                    const [profRaw, rStateRes] = await Promise.all([
                        env.ARAF_KV.get(`USER_PROFILE:${uid}`),
                        (async () => {
                            try {
                                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(uid));
                                const r = await doStub.fetch("https://guard/get-risk-state", { method: "POST" });
                                return await r.json();
                            } catch (e) {
                                return null;
                            }
                        })()
                    ]);

                    const prof = profRaw ? JSON.parse(profRaw).profile : {};
                    return {
                        ...dec,
                        authUid: uid,
                        isVipFree: prof?.isVipFree === true,
                        tier: (prof?.isVipFree ? "VIP_LIFETIME" : (Date.parse(prof?.subscriptionExpiresAt || 0) > Date.now() ? "VIP_TRIAL" : "STANDARD")),
                        subscriptionExpiresAt: prof?.subscriptionExpiresAt || null,
                        riskState: rStateRes?.risk || null
                    };
                }));

                return jsonResponse({ success: true, applications: applications.filter(Boolean) }, 200, cors, correlationId);
            }

            if (target === "admin_kyc_decision") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const adm = await validateAdminAccess(request, env);
                if (!adm.ok) return jsonResponse({ error: "Forbidden" }, 403, cors, correlationId);

                const { traderUid, decision } = body;
                if (!traderUid || !["APPROVE_TRIAL", "APPROVE_LIFETIME_FREE", "REJECT"].includes(decision)) {
                    return jsonResponse({ success: false, message: "Invalid parameters" }, 400, cors, correlationId);
                }

                const uidMapRaw = await env.ARAF_KV.get(`UID_MAP:${traderUid}`);
                const ownerUid = uidMapRaw ? JSON.parse(uidMapRaw).uid : traderUid;

                const kycRaw = await env.ARAF_KV.get(`KYC:${ownerUid}`);
                if (!kycRaw) return jsonResponse({ success: false, message: "KYC profile not found" }, 404, cors, correlationId);

                const kyc = JSON.parse(kycRaw);
                const profRaw = await env.ARAF_KV.get(`USER_PROFILE:${ownerUid}`);
                const userObj = profRaw ? JSON.parse(profRaw) : { profile: {}, preferences: {} };

                let newKycStatus = "APPROVED";
                if (decision === "REJECT") {
                    newKycStatus = "REJECTED";
                } else if (decision === "APPROVE_LIFETIME_FREE") {
                    userObj.profile.isVipFree = true;
                } else if (decision === "APPROVE_TRIAL") {
                    userObj.profile.subscriptionExpiresAt = new Date(Date.now() + 30 * 86400 * 1000).toISOString();
                }

                kyc.kycStatus = newKycStatus;
                kyc.reviewedBy = adm.actor;
                kyc.reviewedAt = Date.now();
                userObj.profile.kycStatus = newKycStatus;

                await Promise.all([
                    env.ARAF_KV.put(`KYC:${ownerUid}`, JSON.stringify(kyc)),
                    env.ARAF_KV.put(`USER_PROFILE:${ownerUid}`, JSON.stringify(userObj)),
                    env.ARAF_KV.put(`AUDIT:${Date.now()}:${ownerUid}`, JSON.stringify({
                        action: `KYC_${decision}`,
                        traderUid: kyc.traderUid || traderUid,
                        targetAuthUid: ownerUid,
                        actor: adm.actor,
                        timestamp: Date.now()
                    }))
                ]);

                auditLog("INFO", correlationId, "ADMIN_KYC_DECISION", { traderUid, decision, actor: adm.actor });
                return jsonResponse({ success: true, decision, kycStatus: newKycStatus }, 200, cors, correlationId);
            }

            if (target === "admin_set_risk_config") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const adm = await validateAdminAccess(request, env);
                if (!adm.ok) return jsonResponse({ error: "Forbidden" }, 403, cors, correlationId);

                const { traderUid, capitalInr, maxDailyLossPct, maxTradesPerDay } = body;
                const uidMapRaw = await env.ARAF_KV.get(`UID_MAP:${traderUid}`);
                const ownerUid = uidMapRaw ? JSON.parse(uidMapRaw).uid : traderUid;

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(ownerUid));
                const rRes = await doStub.fetch("https://guard/set-risk-config", {
                    method: "POST",
                    body: JSON.stringify({ ownerUid, capitalInr, maxDailyLossPct, maxTradesPerDay, isAdminOverride: true })
                });
                const rData = await rRes.json().catch(() => ({}));
                if (!rRes.ok || rData.success !== true) {
                    return jsonResponse({
                        success: false,
                        message: rData.message || "Failed to update risk limits"
                    }, rRes.ok ? 400 : rRes.status, cors, correlationId);
                }

                await env.ARAF_KV.put(`AUDIT:${Date.now()}:${ownerUid}`, JSON.stringify({
                    action: "UPDATE_RISK_LIMITS",
                    traderUid,
                    targetAuthUid: ownerUid,
                    actor: adm.actor,
                    timestamp: Date.now()
                }));

                return jsonResponse({ success: true, ...rData }, 200, cors, correlationId);
            }

            if (target === "admin_reset_killswitch") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const adm = await validateAdminAccess(request, env);
                if (!adm.ok) return jsonResponse({ error: "Forbidden" }, 403, cors, correlationId);

                const { traderUid } = body;
                const uidMapRaw = await env.ARAF_KV.get(`UID_MAP:${traderUid}`);
                const ownerUid = uidMapRaw ? JSON.parse(uidMapRaw).uid : traderUid;

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(ownerUid));
                const rRes = await doStub.fetch("https://guard/admin-reset-lock", { method: "POST" });
                const rData = await rRes.json().catch(() => ({}));
                if (!rRes.ok || rData.success !== true) {
                    return jsonResponse({
                        success: false,
                        message: rData.message || "Failed to reset kill-switch"
                    }, rRes.ok ? 400 : rRes.status, cors, correlationId);
                }

                await env.ARAF_KV.put(`AUDIT:${Date.now()}:${ownerUid}`, JSON.stringify({
                    action: "UNLOCK_KILL_SWITCH",
                    traderUid,
                    targetAuthUid: ownerUid,
                    actor: adm.actor,
                    timestamp: Date.now()
                }));

                auditLog("INFO", correlationId, "ADMIN_KILL_SWITCH_RESET", { traderUid, actor: adm.actor });
                return jsonResponse({ success: true, ...rData }, 200, cors, correlationId);
            }

            if (target === "admin_audit_logs") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const adm = await validateAdminAccess(request, env);
                if (!adm.ok) return jsonResponse({ error: "Forbidden" }, 403, cors, correlationId);

                const auditKeys = [];
                let auditCursor = undefined;
                do {
                    const page = await env.ARAF_KV.list({
                        prefix: "AUDIT:",
                        limit: 100,
                        ...(auditCursor ? { cursor: auditCursor } : {})
                    });
                    auditKeys.push(...page.keys);
                    auditCursor = page.list_complete ? undefined : page.cursor;
                } while (auditCursor);
                auditKeys.sort((a, b) => String(b.name).localeCompare(String(a.name)));

                const logs = await Promise.all(auditKeys.slice(0, 100).map(async (k) => {
                    const raw = await env.ARAF_KV.get(k.name);
                    return raw ? JSON.parse(raw) : null;
                }));

                const validLogs = logs.filter(Boolean);
                validLogs.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
                return jsonResponse({ success: true, logs: validLogs }, 200, cors, correlationId);
            }

            if (target === "coindcx_market_feed") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const vault = await safeReadVault(env, authUser.uid);
                if (!vault.coindcxKey || !vault.coindcxSecret) {
                    return jsonResponse({ success: false, coindcxConnected: false, reason: "COINDCX_NOT_CONNECTED" }, 200, cors, correlationId);
                }

                const [tickRes, futRes, obBtc, obEth, obPaxg] = await Promise.allSettled([
                    fetch("https://api.coindcx.com/exchange/ticker"),
                    fetch("https://public.coindcx.com/market_data/v3/current_prices/futures/rt"),
                    fetch("https://public.coindcx.com/market_data/orderbook?pair=B-BTC_USDT"),
                    fetch("https://public.coindcx.com/market_data/orderbook?pair=B-ETH_USDT"),
                    fetch("https://public.coindcx.com/market_data/orderbook?pair=B-PAXG_USDT")
                ]);

                const ticks = tickRes.status === "fulfilled" && tickRes.value.ok ? await tickRes.value.json().catch(() => []) : [];
                const futJson = futRes.status === "fulfilled" && futRes.value.ok ? await futRes.value.json().catch(() => ({})) : {};
                const futPrices = futJson?.prices || futJson || {};

                const mapDepth = async (resPromise) => {
                    if (resPromise.status !== "fulfilled" || !resPromise.value.ok) return null;
                    const ob = await resPromise.value.json().catch(() => null);
                    if (!ob || !ob.bids || !ob.asks) return null;
                    const bids = Object.entries(ob.bids).sort((a, b) => Number(b[0]) - Number(a[0])).slice(0, 20);
                    const asks = Object.entries(ob.asks).sort((a, b) => Number(a[0]) - Number(b[0])).slice(0, 20);
                    const bidNotional = bids.reduce((s, [p, q]) => s + (Number(p) * Number(q)), 0);
                    const askNotional = asks.reduce((s, [p, q]) => s + (Number(p) * Number(q)), 0);
                    const total = bidNotional + askNotional;
                    if (total <= 0) return null;
                    const bPct = Math.round((bidNotional / total) * 100);
                    const bestBid = Math.max(...bids.map(([p]) => Number(p)));
                    const bestAsk = Math.min(...asks.map(([p]) => Number(p)));
                    return { buyers: bPct, sellers: 100 - bPct, bestBid, bestAsk, depthNotional: total, hasL2: true, source: "COINDCX_L2" };
                };

                const [dBtc, dEth, dPaxg] = await Promise.all([mapDepth(obBtc), mapDepth(obEth), mapDepth(obPaxg)]);

                const pickQuote = (sym, futKey, depthObj) => {
                    const tk = Array.isArray(ticks) ? ticks.find(t => t.market === sym) : null;
                    const fp = futPrices[futKey] || {};
                    const price = Number(fp.ls || fp.mp || tk?.last_price || 0);
                    if (!Number.isFinite(price) || price <= 0) return null;
                    const chg = Number(tk?.change_24_hour || 0);
                    return {
                        price,
                        high: Number(tk?.high || price),
                        low: Number(tk?.low || price),
                        change: `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`,
                        volume: Number(tk?.volume || 0),
                        depth: depthObj,
                        event_time: Date.now(),
                        quality: "LIVE",
                        source: "COINDCX_DIRECT"
                    };
                };

                const quotes = {
                    btcusdt: pickQuote("BTCUSDT", "B-BTC_USDT", dBtc),
                    ethusdt: pickQuote("ETHUSDT", "B-ETH_USDT", dEth),
                    goldmcx: pickQuote("PAXGUSDT", "B-PAXG_USDT", dPaxg)
                };

                const allUsdt = (Array.isArray(ticks) ? ticks : [])
                    .filter(t => t.market && t.market.endsWith("USDT") && Number(t.last_price || 0) > 0)
                    .slice(0, 120)
                    .map(t => {
                        const lp = Number(t.last_price);
                        const chg = Number(t.change_24_hour || 0);
                        return {
                            orderKey: t.market.toUpperCase(),
                            price: lp,
                            high: Number(t.high || lp),
                            low: Number(t.low || lp),
                            changePct: Number(chg.toFixed(2)),
                            volume: Number(t.volume || 0)
                        };
                    });

                return jsonResponse({ success: true, coindcxConnected: true, quotes, allUsdt }, 200, cors, correlationId);
            }

            if (target === "get_auto_trade_state") {
                if (request.method !== "GET") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                const gRes = await doStub.fetch("https://guard/get-auto-trade-state", { method: "POST" });
                return jsonResponse(await gRes.json(), 200, cors, correlationId);
            }

            if (target === "set_auto_trade_config") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ error: kycChk.message }, kycChk.status, cors, correlationId);

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                const gRes = await doStub.fetch("https://guard/set-auto-trade-config", {
                    method: "POST",
                    body: JSON.stringify({
                        enabled: body.enabled,
                        dailyBudgetInr: body.dailyBudgetInr,
                        allowedMarkets: body.allowedMarkets,
                        actor: authUser.email || authUser.uid
                    })
                });
                const gData = await gRes.json();
                await env.ARAF_KV.put(`AUDIT:${Date.now()}:${authUser.uid}`, JSON.stringify({
                    action: "AUTO_TRADE_CONFIG_UPDATE",
                    enabled: gData?.autoTrade?.enabled,
                    dailyBudgetInr: gData?.autoTrade?.dailyBudgetInr,
                    actor: authUser.email || authUser.uid,
                    timestamp: Date.now()
                }));
                return jsonResponse(gData, gRes.status, cors, correlationId);
            }

            if (target === "execute_auto_trade") {
                if (request.method !== "POST") return jsonResponse({ success: false, code: "METHOD_NOT_ALLOWED" }, 405, cors, correlationId);
                const authUser = await verifyFirebaseToken(request, env);
                const kycChk = await requireApprovedUser(env, authUser);
                if (!kycChk.ok) return jsonResponse({ success: false, error: kycChk.message }, kycChk.status, cors, correlationId);

                if (!(await requirePinSession(request, env, authUser.uid))) {
                    return jsonResponse({ success: false, code: "PIN_SESSION_REQUIRED", error: "Server PIN unlock required for Auto Trade" }, 403, cors, correlationId);
                }

                const clientOrderId = request.headers.get("X-Client-Order-Id");
                if (typeof clientOrderId !== "string" || !/^[A-Za-z0-9_-]{8,64}$/.test(clientOrderId)) {
                    return jsonResponse({ success: false, error: "Valid X-Client-Order-Id required" }, 400, cors, correlationId);
                }

                // Hard Gate 1: Score >= 90 & 100% Applicable Engine Pass & Live Data Verified
                const score = Number(body.score || 0);
                if (!Number.isFinite(score) || score < 90 || body.allRequiredEnginesPass !== true) {
                    return jsonResponse({ success: false, code: "ENGINE_GATE_REJECTED", error: "Auto Trade requires Score >= 90/100 and 100% applicable engine PASS." }, 403, cors, correlationId);
                }
                if (body.dataQuality !== "LIVE") {
                    return jsonResponse({ success: false, code: "DATA_NOT_LIVE", error: `Auto Trade blocked: dataQuality is ${body.dataQuality || "UNVERIFIED"} (LIVE required).` }, 403, cors, correlationId);
                }

                const mode = body.mode === "PAPER" ? "PAPER" : "LIVE";
                if (mode === "LIVE" && env.LIVE_TRADING_ENABLED !== "true") {
                    return jsonResponse({ success: false, code: "LIVE_TRADING_DISABLED", error: "Live automated trading disabled on server." }, 403, cors, correlationId);
                }

                const upperSym = String(body.symbol || "").trim().toUpperCase();
                const side = body.side === "SELL" ? "SELL" : (body.side === "BUY" ? "BUY" : null);
                const qty = Number(body.qty);
                const slPrice = Number(body.slPrice);
                const tpPrice = Number(body.tpPrice);
                const requiredInr = Number(body.requiredInr);
                const assetCategory = body.assetCategory === "crypto" ? "crypto" : "indian";

                if (!upperSym || !side || !Number.isFinite(qty) || qty <= 0 || !Number.isFinite(slPrice) || slPrice <= 0 || !Number.isFinite(tpPrice) || tpPrice <= 0) {
                    return jsonResponse({ success: false, error: "Invalid Auto Trade order parameters (Entry/SL/TP/Qty required)." }, 400, cors, correlationId);
                }

                const doStub = env.RISK_GUARD.get(env.RISK_GUARD.idFromName(authUser.uid));
                // Hard Gate 2: Atomic Daily Budget Reservation
                const resBud = await doStub.fetch("https://guard/reserve-auto-trade-budget", {
                    method: "POST",
                    body: JSON.stringify({ clientOrderId, requiredInr, assetCategory })
                });
                const resBudJson = await resBud.json();
                if (!resBud.ok || !resBudJson.allowed) {
                    return jsonResponse({ success: false, code: "AUTO_BUDGET_BLOCKED", error: resBudJson.reason || "Auto Trade budget/switch blocked." }, resBud.status, cors, correlationId);
                }

                // Hard Gate 3: Risk & Idempotency Lock
                const guardRes = await doStub.fetch("https://guard/precheck-and-lock", {
                    method: "POST",
                    body: JSON.stringify({ ownerUid: authUser.uid, clientOrderId, operationType: "ENTRY", symbol: upperSym, side, qty, mode, isAutoTrade: true })
                });
                const guardData = await guardRes.json();
                if (!guardRes.ok || !guardData.allowed) {
                    await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                    return jsonResponse({ success: false, code: "RISK_OR_IDEM_BLOCKED", error: guardData.reason || "Daily risk guard blocked." }, guardRes.status, cors, correlationId);
                }

                if (mode === "PAPER") {
                    const paperId = `AUTO_PPR_${Date.now()}`;
                    await Promise.all([
                        doStub.fetch("https://guard/update-order-state", { method: "POST", body: JSON.stringify({ clientOrderId, state: "PAPER_FILLED", brokerOrderId: paperId, filledQty: qty, paperUpdate: { symbol: upperSym, side, qty } }) }),
                        doStub.fetch("https://guard/commit-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) })
                    ]);
                    return jsonResponse({ success: true, mode: "PAPER", state: "PAPER_FILLED", brokerOrderId: paperId, autoTrade: resBudJson.autoTrade, risk: guardData.risk }, 200, cors, correlationId);
                }

                const vault = await safeReadVault(env, authUser.uid);
                let spec = SERVER_CANONICAL_INSTRUMENTS[upperSym];
                if (!spec) {
                    if (/^[A-Z0-9]{2,15}USDT$/.test(upperSym)) {
                        const base = upperSym.replace(/USDT$/, "");
                        spec = { broker: "COINDCX", marketPair: `B-${base}_USDT`, minQty: 0.001, maxQty: 500000, maxLeverage: 5 };
                    } else if (/^[A-Z0-9&-]{2,20}$/.test(upperSym)) {
                        const ex = body.exchange === "BSE" ? "BSE" : "NSE";
                        spec = { broker: "SHOONYA", exchange: ex, segment: "EQ", lotSize: 1, maxQty: 5000, tickSize: 0.05, token: "DYNAMIC" };
                    }
                }
                if (!spec) {
                    await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                    await doStub.fetch("https://guard/rollback-lock", { method: "POST", body: JSON.stringify({ clientOrderId, operationType: "ENTRY", state: "REJECTED", reason: "Unsupported symbol" }) });
                    return jsonResponse({ success: false, error: `Unsupported symbol ${upperSym}` }, 400, cors, correlationId);
                }

                if (spec.broker === "SHOONYA") {
                    if (!vault.shoonyaUid || !vault.shoonyaToken) {
                        await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                        await doStub.fetch("https://guard/rollback-lock", { method: "POST", body: JSON.stringify({ clientOrderId, operationType: "ENTRY", state: "REJECTED", reason: "Shoonya unlinked" }) });
                        return jsonResponse({ success: false, error: "Shoonya not connected" }, 400, cors, correlationId);
                    }
                    const verifiedContract = await resolveVerifiedShoonyaContract(env, authUser.uid, vault, upperSym, spec, getIstDateStr());
                    if (!verifiedContract) {
                        await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                        await doStub.fetch("https://guard/rollback-lock", { method: "POST", body: JSON.stringify({ clientOrderId, operationType: "ENTRY", state: "REJECTED", reason: "Contract master unavailable" }) });
                        return jsonResponse({ success: false, error: "Shoonya contract verification failed" }, 503, cors, correlationId);
                    }

                    const sRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PlaceOrder", {
                        method: "POST",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: `jData=${encodeURIComponent(JSON.stringify({
                            uid: vault.shoonyaUid, actid: vault.shoonyaUid, exch: verifiedContract.exch, tsym: verifiedContract.tsym,
                            qty: String(qty), prc: "0", prd: "I", trantype: side === "BUY" ? "B" : "S", prctyp: "MKT", ret: "DAY"
                        }))}&jKey=${vault.shoonyaToken}`
                    });
                    const d = await sRes.json().catch(() => ({}));
                    if (d?.stat === "Ok" && d?.norenordno) {
                        const chk = await verifyShoonyaOrderStatus(env, authUser.uid, vault, d.norenordno, 3);
                        const filled = chk.filledQty > 0 ? chk.filledQty : (chk.status === "FILLED" ? qty : 0);
                        if (filled > 0) {
                            const slSide = side === "BUY" ? "S" : "B";
                            const roundedTrg = roundToTick(slPrice, verifiedContract.tickSize);
                            const roundedPrc = roundToTick(slSide === "S" ? roundedTrg * 0.995 : roundedTrg * 1.005, verifiedContract.tickSize);
                            const slRes = await brokerFetch(env, authUser.uid, "https://api.shoonya.com/NorenWClientTP/PlaceOrder", {
                                method: "POST",
                                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                                body: `jData=${encodeURIComponent(JSON.stringify({
                                    uid: vault.shoonyaUid, actid: vault.shoonyaUid, exch: verifiedContract.exch, tsym: verifiedContract.tsym,
                                    qty: String(filled), prc: String(roundedPrc), trgprc: String(roundedTrg),
                                    prd: "I", trantype: slSide, prctyp: "SL-LMT", ret: "DAY"
                                }))}&jKey=${vault.shoonyaToken}`
                            });
                            const slJson = await slRes.json().catch(() => ({}));
                            if (slJson?.stat === "Ok" && slJson?.norenordno) {
                                await Promise.all([
                                    doStub.fetch("https://guard/update-order-state", { method: "POST", body: JSON.stringify({ clientOrderId, state: chk.status, brokerOrderId: d.norenordno, avgPrice: chk.avgPrice, filledQty: filled, scheduleAlarm: true }) }),
                                    doStub.fetch("https://guard/commit-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) })
                                ]);
                                return jsonResponse({ success: true, mode: "LIVE", state: chk.status, brokerOrderId: d.norenordno, slOrderId: slJson.norenordno, protectionStatus: "AUTO_SL_ACTIVE", risk: guardData.risk }, 200, cors, correlationId);
                            }
                            await doStub.fetch("https://guard/critical-unprotected-lock", { method: "POST", body: JSON.stringify({ reason: "AUTO_SL_FAILED_LOCK" }) });
                            return jsonResponse({ success: false, error: "Auto Entry filled but SL failed — entries locked!" }, 502, cors, correlationId);
                        }
                    }
                    await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                    await doStub.fetch("https://guard/rollback-lock", { method: "POST", body: JSON.stringify({ clientOrderId, operationType: "ENTRY", state: "REJECTED", reason: d?.emsg || "Shoonya rejected" }) });
                    return jsonResponse({ success: false, error: d?.emsg || "Shoonya rejected Auto order" }, 400, cors, correlationId);
                }

                if (spec.broker === "COINDCX") {
                    if (!vault.coindcxKey || !vault.coindcxSecret) {
                        await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                        await doStub.fetch("https://guard/rollback-lock", { method: "POST", body: JSON.stringify({ clientOrderId, operationType: "ENTRY", state: "REJECTED", reason: "CoinDCX unlinked" }) });
                        return jsonResponse({ success: false, error: "CoinDCX not connected" }, 400, cors, correlationId);
                    }
                    const payloadStr = JSON.stringify({
                        timestamp: Date.now(),
                        order: {
                            side: side.toLowerCase(), pair: spec.marketPair, total_quantity: qty,
                            leverage: Math.min(5, Number(body.leverage || 1)), order_type: "market_order",
                            margin_currency_short_name: "USDT", client_order_id: clientOrderId,
                            stop_loss_price: slPrice, take_profit_price: tpPrice
                        }
                    });
                    const sig = await generateHmacSha256(vault.coindcxSecret, payloadStr);
                    const dRes = await brokerFetch(env, authUser.uid, "https://api.coindcx.com/exchange/v1/derivatives/futures/orders/create", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", "X-AUTH-APIKEY": vault.coindcxKey, "X-AUTH-SIGNATURE": sig },
                        body: payloadStr
                    });
                    const d = await dRes.json().catch(() => ({}));
                    const ordId = d?.id || d?.order_id || (Array.isArray(d) ? d[0]?.id : null);
                    if (dRes.ok && ordId) {
                        await Promise.all([
                            doStub.fetch("https://guard/update-order-state", { method: "POST", body: JSON.stringify({ clientOrderId, state: "ACCEPTED", brokerOrderId: ordId, scheduleAlarm: true }) }),
                            doStub.fetch("https://guard/commit-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) })
                        ]);
                        return jsonResponse({ success: true, mode: "LIVE", state: "ACCEPTED", brokerOrderId: ordId, protectionStatus: "AUTO_SL_TP_ATTACHED", risk: guardData.risk }, 200, cors, correlationId);
                    }
                    await doStub.fetch("https://guard/release-auto-trade-budget", { method: "POST", body: JSON.stringify({ clientOrderId }) });
                    await doStub.fetch("https://guard/rollback-lock", { method: "POST", body: JSON.stringify({ clientOrderId, operationType: "ENTRY", state: "REJECTED", reason: d?.message || "CoinDCX rejected" }) });
                    return jsonResponse({ success: false, error: d?.message || "CoinDCX rejected Auto order" }, 400, cors, correlationId);
                }
            }

            return jsonResponse({ success: false, code: "UNSUPPORTED_ROUTE", error: "Unknown gateway target", target }, 404, cors, correlationId);

        } catch (err) {
            auditLog("ERROR", correlationId, "GATEWAY_ERROR", { message: err.message, code: err.code || "INTERNAL_ERROR" });
            if (/kv put|kv write|limit exceeded/i.test(String(err.message || ""))) {
                if (target === "news_feed" || target === "ipo_feed" || target === "ipo_news_feed") {
                    return jsonResponse({
                        success: false,
                        code: "FREE_TIER_WRITE_LIMIT",
                        status: "DEGRADED",
                        message: "Live cloud cache write limit reached. Use cached/public feed until UTC reset."
                    }, 503, cors, correlationId);
                }
                if (target === "verify_pin" || target === "place_order" || target === "execute_auto_trade") {
                    return jsonResponse({
                        success: false,
                        code: "KV_WRITE_LIMIT",
                        error: "Trading lock storage is temporarily limited. Existing Durable Object trades remain protected; retry after reset."
                    }, 503, cors, correlationId);
                }
            }
            const status = err instanceof ApiError ? err.status : 500;
            const code = err instanceof ApiError ? err.code : "INTERNAL_GATEWAY_ERROR";
            return jsonResponse({ error: err.message || "Internal Gateway Error", code, correlationId }, status, cors, correlationId);
        }
    }
};
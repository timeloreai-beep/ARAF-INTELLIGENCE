/* ==========================================================================
   ARAF INTELLIGENCE — UNIFIED STATIC IP BROKER RELAY (araf-relay.js)
   Supports: Shoonya (NorenWClientTP) & CoinDCX APIs with 12s Timeout Guard
   ========================================================================== */

const http = require("http");
const https = require("https");
const crypto = require("crypto");

const PORT = process.env.PORT || 8787;
const RELAY_SECRET = process.env.BROKER_RELAY_SECRET || "";
const MAX_BODY_BYTES = 1024 * 1024;
const seenNonces = new Map();
const httpAgent = new http.Agent({ keepAlive: true });
const httpsAgent = new https.Agent({ keepAlive: true });

if (!RELAY_SECRET || RELAY_SECRET.length < 16) {
    console.error("❌ FATAL: BROKER_RELAY_SECRET is missing or too short (<16 chars). Relay will FAIL-CLOSED!");
}

function safeEqual(a, b) {
    const bufA = Buffer.from(String(a || ""), "utf8");
    const bufB = Buffer.from(String(b || ""), "utf8");
    if (bufA.length !== bufB.length) return false;
    return crypto.timingSafeEqual(bufA, bufB);
}

setInterval(() => {
    const now = Date.now();
    for (const [n, exp] of seenNonces.entries()) {
        if (exp <= now) seenNonces.delete(n);
    }
}, 60000).unref();

http.createServer((req, res) => {
    if (req.method === "GET") {
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ ok: Boolean(RELAY_SECRET && RELAY_SECRET.length >= 16), service: "ARAF Unified Static IP Relay", status: RELAY_SECRET ? "ARMED" : "FAIL_CLOSED" }));
    }

    if (!RELAY_SECRET || RELAY_SECRET.length < 16) {
        res.writeHead(503, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ stat: "Not_Ok", error: "Relay Misconfigured (Fail-Closed)" }));
    }

    if (req.method !== "POST" || !safeEqual(req.headers["x-relay-secret"], RELAY_SECRET)) {
        res.writeHead(403, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ stat: "Not_Ok", error: "Unauthorized Relay Access" }));
    }

    const reqTs = Number(req.headers["x-relay-timestamp"]);
    const reqNonce = String(req.headers["x-relay-nonce"] || "");
    if (!Number.isFinite(reqTs) || Math.abs(Date.now() - reqTs) > 60000 || reqNonce.length < 16 || seenNonces.has(reqNonce)) {
        res.writeHead(403, { "Content-Type": "application/json" });
        return res.end(JSON.stringify({ stat: "Not_Ok", error: "Replay or Expired Relay Timestamp Rejected" }));
    }
    seenNonces.set(reqNonce, Date.now() + 120000);

    let rawBody = "";
    let totalBytes = 0;
    let aborted = false;

    req.on("data", chunk => {
        totalBytes += chunk.length;
        if (totalBytes > MAX_BODY_BYTES) {
            aborted = true;
            res.writeHead(413, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ stat: "Not_Ok", error: "Payload too large" }));
            req.destroy();
            return;
        }
        rawBody += chunk;
    });

    req.on("end", async () => {
        if (aborted) return;
        try {
            const { targetUrl, method = "POST", headers = {}, body } = JSON.parse(rawBody || "{}");
            const isAllowed = typeof targetUrl === "string" && (
                targetUrl.startsWith("https://api.shoonya.com/") ||
                targetUrl.startsWith("https://api.coindcx.com/")
            );

            if (!isAllowed) {
                res.writeHead(400, { "Content-Type": "application/json" });
                return res.end(JSON.stringify({ stat: "Not_Ok", error: "Forbidden Broker Target URL" }));
            }

            const cleanHeaders = {};
            if (headers && typeof headers === "object") {
                for (const [k, v] of Object.entries(headers)) {
                    const lk = k.toLowerCase();
                    if (lk !== "host" && lk !== "content-length" && lk !== "connection") {
                        cleanHeaders[k] = v;
                    }
                }
            }

            const isBodyAllowed = method !== "GET" && method !== "HEAD";
            const upstreamUrl = new URL(targetUrl);
            const transport = upstreamUrl.protocol === "https:" ? https : http;
            const agent = upstreamUrl.protocol === "https:" ? httpsAgent : httpAgent;

            const upstreamResult = await new Promise((resolve, reject) => {
                const upstreamReq = transport.request(upstreamUrl, {
                    method,
                    headers: cleanHeaders,
                    agent,
                    timeout: 12000
                }, upstream => {
                    let respText = "";
                    upstream.setEncoding("utf8");
                    upstream.on("data", chunk => { respText += chunk; });
                    upstream.on("end", () => resolve({
                        status: upstream.statusCode || 502,
                        contentType: upstream.headers["content-type"] || "application/json",
                        respText
                    }));
                });
                upstreamReq.on("timeout", () => upstreamReq.destroy(new Error("Upstream timeout")));
                upstreamReq.on("error", reject);
                if (isBodyAllowed && body !== undefined) {
                    upstreamReq.write(typeof body === "string" ? body : JSON.stringify(body));
                }
                upstreamReq.end();
            });

            res.writeHead(upstreamResult.status, { "Content-Type": upstreamResult.contentType });
            res.end(upstreamResult.respText);
        } catch (err) {
            const status = err.name === "TimeoutError" || err.name === "AbortError" ? 504 : 502;
            const errMsg = err.message || "Upstream relay error";
            res.writeHead(status, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ stat: "Not_Ok", emsg: errMsg, error: errMsg }));
        }
    });
}).listen(PORT, () => console.log(`✅ Araf Relay active on port ${PORT}`));
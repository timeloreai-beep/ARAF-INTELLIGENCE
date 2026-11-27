import logging
import json
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai_guardian", tags=["ai_guardian"])


def _build_guardian_prompt(query: str, context: dict[str, Any], history: list[dict]) -> str:
    """Build a dynamic prompt for the AI Guardian using live market context and conversation history."""
    asset_name = context.get("asset", "the asset")
    ltp = context.get("ltp", "N/A")
    regime = context.get("regime", "UNKNOWN")
    two_way = context.get("twoWaySignal", "NO TRADE")
    ema9 = context.get("ema9", "UNAVAILABLE")
    ema21 = context.get("ema21", "UNAVAILABLE")
    rsi14 = context.get("rsi14", "UNAVAILABLE")
    atr = context.get("atr", "UNAVAILABLE")
    realized_vol = context.get("realizedVol", "UNAVAILABLE")
    vwap = context.get("vwap", "UNAVAILABLE")
    corr_btc = context.get("corrBtc", "UNAVAILABLE")
    s1 = context.get("s1", "N/A")
    r1 = context.get("r1", "N/A")
    vix = context.get("vix", "UNAVAILABLE")
    latest_catalysts = context.get("latestCatalysts", "No recent catalysts")

    dir_signal = two_way.split()[0] if two_way else "WAIT"
    rr_ratio = two_way.split()[-1] if len(two_way.split()) > 1 else "--"

    # Build conversation history block
    history_block = ""
    if history:
        history_lines = ["RECENT CONVERSATION HISTORY:"]
        for msg in history[-4:]:
            role = "User" if msg.get("role") == "user" else "Guardian"
            content = msg.get("content", "")[:200]
            history_lines.append(f"{role}: {content}")
        history_block = "\n".join(history_lines) + "\n\n"

    context_block = f"""
LIVE MARKET CONTEXT:
- Asset: {asset_name}
- LTP: {ltp}
- Current Regime: {regime}
- Two-Way Signal: {two_way} (Direction: {dir_signal}, R:R: {rr_ratio})
- EMA 9: {ema9} | EMA 21: {ema21}
- RSI 14: {rsi14}
- ATR: {atr}
- Realized Volatility: {realized_vol}
- VWAP: {vwap}
- Correlation with BTC: {corr_btc}
- Support (S1): {s1} | Resistance (R1): {r1}
- India VIX: {vix}
- Latest Catalysts: {latest_catalysts}

{history_block}USER QUERY: {query}

INSTRUCTION:
You are ARAF INTELLIGENCE Guardian, a knowledgeable trading assistant. Answer the user's question based on the live market context above and conversation history.

CRITICAL RULES:
1. Generate a FRESH, UNIQUE response for THIS specific query - NEVER repeat generic templates or canned phrases.
2. If Bengali requested (langName contains "bn"), respond in 100% fluent, natural Bengali (পাকা ও শুদ্ধ বাংলা) - ZERO English words.
3. Reference SPECIFIC numbers from context (LTP, RSI, regime, S1/R1, catalysts) - don't say "current levels" or vague terms.
4. If context is insufficient, say exactly: "আমার কাছে এখনো পর্যাপ্ত লাইভ ডেটা নেই।" / "Insufficient live data to answer precisely."
5. Be concise, actionable, trader-focused. No disclaimers, no "as an AI" phrases, no generic advice.
6. If user asks follow-up, reference previous answer naturally using conversation history.
"""

    return context_block


async def _call_gemini_with_bengali(settings, prompt: str, lang_name: str, max_retries: int = 2) -> str:
    """Call Gemini API with Bengali enforcement via retry."""
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    api_key = settings.gemini_api_key
    headers = {"Content-Type": "application/json"}
    url = f"{endpoint}?key={api_key}"

    for attempt in range(max_retries):
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        async with __import__("httpx").AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=20)
            if resp.status_code != 200:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Gemini API failed: {resp.status_code}")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            data = resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            if attempt == max_retries - 1:
                raise RuntimeError("No response from Gemini API")
            await asyncio.sleep(0.5 * (attempt + 1))
            continue

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            if attempt == max_retries - 1:
                raise RuntimeError("No content parts in Gemini response")
            await asyncio.sleep(0.5 * (attempt + 1))
            continue

        reply = parts[0].get("text", "").strip()
        if not reply:
            if attempt == max_retries - 1:
                raise RuntimeError("Empty response from Gemini API")
            await asyncio.sleep(0.5 * (attempt + 1))
            continue

        # Bengali enforcement check
        if lang_name and "bn" in str(lang_name).lower():
            bangla_ratio = sum(1 for c in reply if "\u0980" <= c <= "\u09FF") / max(len(reply), 1)
            if bangla_ratio >= 0.5:
                return reply
            # Strengthen Bengali instruction for retry
            prompt += "\n\n[STRICT: আপনার উত্তর শুধুমাত্র পাকা ও শুদ্ধ বাংলা (পূর্ণাঙ্গ বাংলা লিপিতে) হতে হবে। কোনো ইংরেজি শব্দ বা বাক্য ব্যবহার করবেন না। শুধু বাংলা ভাষায় উত্তর দিন।]"
            await asyncio.sleep(0.5)
            continue

        return reply

    # Return best attempt even if Bengali check didn't pass
    return reply


@router.post("", response_model=dict)
async def ai_guardian_endpoint(request: Request) -> JSONResponse:
    """AI Guardian endpoint - directly wired to Gemini Flash API with live context."""
    settings = get_settings()
    body = await request.json()

    query = body.get("query", "")
    lang_name = body.get("langName", "English")
    context = body.get("context", {})
    history = body.get("history", [])

    if not query:
        return JSONResponse(status_code=400, content={"success": False, "reply": "No query provided"})

    if not settings.gemini_api_key:
        return JSONResponse(
            status_code=503,
            content={"success": False, "reply": "Gemini API key not configured"},
        )

    # Build the prompt with live market context and history
    prompt = _build_guardian_prompt(query, context, history)

    try:
        reply = await _call_gemini_with_bengali(settings, prompt, lang_name)

        return JSONResponse(content={"success": True, "reply": reply})

    except Exception as e:
        logger.warning("AI Guardian Gemini request failed: %s", e)
        # Context-aware error message instead of static fallback
        asset = context.get("asset", "the market")
        regime = context.get("regime", "unknown")
        return JSONResponse(
            status_code=502,
            content={"success": False, "reply": f"AI Guardian temporarily unable to analyze {asset} (regime: {regime}). Please retry in a moment."},
        )
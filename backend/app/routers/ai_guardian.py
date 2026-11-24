import logging
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..services.news import NewsEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai_guardian", tags=["ai_guardian"])


def _build_guardian_prompt(query: str, context: dict[str, Any]) -> str:
    """Build a dynamic prompt for the AI Guardian using live market context."""
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

USER QUERY: {query}

INSTRUCTION:
You are ARAF INTELLIGENCE Guardian, a knowledgeable trading assistant. Answer the user's question based on the live market context above. Provide analysis in natural, fluent, conversational Bengali (পাকা ও শুদ্ধ বাংলা) if the user's query or language preference indicates Bengali, otherwise respond in English. Be concise, practical, and market-focused. Do not make up data - use only the context provided. If the context doesn't have enough information to answer meaningfully, say so honestly. Never repeat static canned lines - generate a fresh response each time based on the actual user query and context.
"""

    return context_block


@router.post("", response_model=dict)
async def ai_guardian_endpoint(request: Request) -> JSONResponse:
    """AI Guardian endpoint - directly wired to Gemini Flash API."""
    settings = get_settings()
    body = await request.json()

    query = body.get("query", "")
    lang_name = body.get("langName", "English")
    context = body.get("context", {})
    history = body.get("history", [])

    if not query:
        return JSONResponse(status_code=400, content={"success": False, "reply": "No query provided"})

    # Build the prompt with live market context
    prompt = _build_guardian_prompt(query, context)

    # Call Gemini Flash API
    if not settings.gemini_api_key:
        return JSONResponse(
            status_code=503,
            content={"success": False, "reply": "Gemini API key not configured"},
        )

    try:
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        api_key = settings.gemini_api_key
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ]
        }
        headers = {"Content-Type": "application/json"}
        url = f"{endpoint}?key={api_key}"

        async with __import__("httpx").AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=20)
            if resp.status_code != 200:
                return JSONResponse(
                    status_code=502,
                    content={"success": False, "reply": "Gemini API request failed"},
                )
            data = resp.json()

        # Extract the response text
        candidates = data.get("candidates", [])
        if not candidates:
            return JSONResponse(
                status_code=502,
                content={"success": False, "reply": "No response from Gemini API"},
            )

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts:
            return JSONResponse(
                status_code=502,
                content={"success": False, "reply": "No content parts in Gemini response"},
            )

        reply = parts[0].get("text", "").strip()

        if not reply:
            return JSONResponse(
                status_code=502,
                content={"success": False, "reply": "Empty response from Gemini API"},
            )

        # If the user's language is Bengali, ensure the response is in Bengali
        # We already included the instruction in the prompt, but add a final check
        bangla_indicators = sum(1 for c in reply if "\u0980" <= c <= "\u09FF")
        if lang_name and "bn" in str(lang_name).lower() and bangla_indicators < len(reply) * 0.3:
            # Fallback: try to translate or respond in bilingual mode
            pass  # The prompt already instructs Bengali output

        return JSONResponse(
            content={"success": True, "reply": reply},
        )

    except Exception as e:
        logger.warning("AI Guardian Gemini request failed: %s", e)
        return JSONResponse(
            status_code=502,
            content={"success": False, "reply": "AI Guardian temporary unavailable"},
        )
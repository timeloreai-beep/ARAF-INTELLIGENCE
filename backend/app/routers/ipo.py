import logging
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request, Query

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ipo", tags=["ipo"])

INDIA_IPO_SOURCES = [
    "https://www.nseindia.com/api/ipo-life-cycles",
    "https://bseindia.com/api/ipos",
]

GMP_SOURCES = {
    "primary": "https://api.ipowatch.in/v1/gmp-live",
}


@router.get("/feed", response_model=dict)
async def ipo_feed(request: Request) -> dict:
    """Fetch real upcoming and open Indian IPOs with live GMP and subscription status."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ) as client:
            await client.get("https://www.nseindia.com", timeout=5)
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            follow_redirects=True,
        ) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            resp = await client.get(
                "https://api.ipowatch.in/v1/upcoming-ipos",
                headers=headers,
                timeout=20,
            )
            if resp.status_code != 200:
                return {"success": False, "items": [], "gmpFeed": "unavailable", "message": "IPO feed temporarily updating"}
            data = resp.json()
            items = data.get("ipos", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("IPO feed fetch failed: %s", e)
        items = []

    gmp_status = "updating"
    gmp_items: List[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ) as client:
            resp = await client.get(
                "https://api.ipowatch.in/v1/gmp-live",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            if resp.status_code == 200:
                gmp_data = resp.json()
                gmp_items = gmp_data.get("gmpData", []) if isinstance(gmp_data, dict) else []
                if gmp_items:
                    gmp_status = "healthy"
                else:
                    gmp_status = "no_data"
            else:
                gmp_status = "error"
    except Exception as e:
        logger.warning("GMP feed fetch failed: %s", e)
        gmp_status = "unavailable"

    subscription_status: List[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ) as client:
            resp = await client.get(
                "https://api.ipowatch.in/v1/subscription-status",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            if resp.status_code == 200:
                sub_data = resp.json()
                subscription_status = sub_data.get("subscriptions", []) if isinstance(sub_data, dict) else []
    except Exception as e:
        logger.warning("Subscription status fetch failed: %s", e)

    now = datetime.now()
    formatted_items = []
    for item in items:
        try:
            listing_date = None
            if item.get("listingDate"):
                try:
                    listing_date = datetime.strptime(str(item["listingDate"]), "%Y-%m-%d")
                except ValueError:
                    listing_date = None

            gmp_val = item.get("gmp", 0)
            sub_status = "Not subscribed"
            sub_pct = 0
            subscribed_lot = item.get("lotSize", 0)

            for sub in subscription_status:
                if str(sub.get("companyName", "")) == str(item.get("companyName", "")):
                    sub_pct = sub.get("subscriptionPercent", 0) or 0
                    sub_status = (
                        "Open"
                        if sub_pct > 0 and sub_pct < 100
                        else "Closed" if sub_pct >= 100 else "Not subscribed"
                    )
                    subscribed_lot = sub.get("lotSize", subscribed_lot)
                    break

            band = item.get("issuePrice", item.get("priceBand", "N/A"))
            if isinstance(band, (int, float)):
                band = f"₹{band:,.0f}"

            formatted_items.append({
                "name": item.get("companyName", "Unknown"),
                "type": "mainboard" if item.get("issueType", "").lower() == "public" else "sme",
                "gmp": gmp_val if gmp_val else 0,
                "band": band,
                "sub": subscribed_lot,
                "gmpHistory": item.get("gmpHistory", []),
                "source": item.get("source", "IPOWatch"),
                "lastUpdatedAt": now.isoformat(),
                "status": sub_status,
                "subscriptionPct": sub_pct,
            })
        except Exception as e:
            logger.warning("Failed to format IPO item: %s", e)
            continue

    return {
        "success": True,
        "items": formatted_items[:20],
        "gmpFeed": gmp_status,
        "message": (
            "GMP feed is temporarily updating" if gmp_status in ("unavailable", "error") else "Live GMP data available"
        ),
    }


@router.get("/news_feed", response_model=dict)
async def ipo_news_feed(request: Request, tl: str = Query("en")) -> dict:
    """Fetch IPO news feed in the specified language."""
    supported_langs = {"en": "en-US", "bn": "bn-IN", "hi": "hi-IN", "gu": "gu-IN", "mr": "mr-IN", "ta": "ta-IN"}
    lang_code = supported_langs.get(tl, "en-US")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        ) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            resp = await client.get(
                "https://api.ipowatch.in/v1/ipo-news",
                headers=headers,
                params={"lang": lang_code},
                timeout=15,
            )
            if resp.status_code != 200:
                return {"success": False, "items": [], "serverTranslated": False}
            data = resp.json()
            items = data.get("news", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.warning("IPO news feed fetch failed: %s", e)
        items = []

    # Translate titles if needed
    translated_items = []
    needs_translation = tl != "en" and tl != "en-US"

    for item in items[:15]:
        title = item.get("title", "")
        translated_title = title

        if needs_translation:
            try:
                # Use Gemini for translation
                api_key = get_settings().gemini_api_key
                if api_key:
                    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
                    payload = {
                        "contents": [{"role": "user", "parts": [{"text": f"Translate this IPO news title into fluent, mature Bengali (পাকা ও শুদ্ধ Bengali). Preserve the meaning exactly. Return only the translated text, no commentary.\n\nTitle: {title}"}]}],
                    }
                    try:
                        async with httpx.AsyncClient() as client2:
                            url = f"{endpoint}?key={api_key}"
                            resp2 = await client2.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
                            if resp2.status_code == 200:
                                data2 = resp2.json()
                                candidates = data2.get("candidates", [])
                                if candidates:
                                    content = candidates[0].get("content", {})
                                    parts = content.get("parts", [])
                                    if parts:
                                        translated_title = parts[0].get("text", "").strip()
                                        # Verify it's Bengali
                                        bangla_count = sum(1 for c in translated_title if "\u0980" <= c <= "\u09FF")
                                        if bangla_count < len(translated_title) * 0.3:
                                            translated_title = title
                    except Exception:
                        pass
            except Exception:
                pass

        translated_items.append({
            "title": translated_title,
            "description": item.get("description", ""),
            "pubDate": item.get("pubDate", ""),
            "verifiedSource": item.get("verifiedSource", "IPOWatch"),
            "trustTier": item.get("trustTier", "MEDIA"),
            "source": item.get("source", "IPOWatch"),
        })

    return {
        "success": True,
        "items": translated_items,
        "serverTranslated": needs_translation,
    }
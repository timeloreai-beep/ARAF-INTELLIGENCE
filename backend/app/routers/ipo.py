import logging
import asyncio
import json
import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Request, Query

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ipo", tags=["ipo"])

# Data source endpoints
NSE_IPO_URL = "https://www.nseindia.com/api/ipo-life-cycles"
BSE_IPO_URL = "https://bseindia.com/api/ipos"
IPOWATCH_UPCOMING = "https://api.ipowatch.in/v1/upcoming-ipos"
IPOWATCH_GMP = "https://api.ipowatch.in/v1/gmp-live"
IPOWATCH_SUBSCRIPTION = "https://api.ipowatch.in/v1/subscription-status"
IPOWATCH_NEWS = "https://api.ipowatch.in/v1/ipo-news"

CHITTORGARH_GMP = "https://www.chittorgarh.com/ipo/gmp/"

# Source priority for fallback
SOURCE_PRIORITY = [
    ("nse_official", "NSE Official", "https://www.nseindia.com/api/ipo-life-cycles"),
    ("bse_official", "BSE Official", "https://bseindia.com/api/ipos"),
    ("ipowatch", "IPOWatch", "https://api.ipowatch.in/v1/upcoming-ipos"),
]

GMP_SOURCES = [
    ("ipowatch", "IPOWatch", "https://api.ipowatch.in/v1/gmp-live"),
    ("chittorgarh", "Chittorgarh", "https://www.chittorgarh.com/ipo/gmp/"),
]

SUBSCRIPTION_SOURCES = [
    ("ipowatch", "IPOWatch", "https://api.ipowatch.in/v1/subscription-status"),
    ("nse_official", "NSE Official", "https://www.nseindia.com/api/ipo-subscription"),
]

NEWS_SOURCES = [
    ("ipowatch", "IPOWatch", "https://api.ipowatch.in/v1/ipo-news"),
]

# Cache TTLs (seconds)
CACHE_TTL_LIVE = 120      # 2 min for live GMP
CACHE_TTL_UPCOMING = 300  # 5 min for upcoming IPOs
CACHE_TTL_SUBSCRIPTION = 300  # 5 min for subscription
CACHE_TTL_NEWS = 300      # 5 min for news

# Request headers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Enhanced headers for Indian financial sites that require additional headers
NSE_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
    "X-Requested-With": "XMLHttpRequest",
}

BSE_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

IPOWATCH_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://www.ipowatch.in/",
    "Origin": "https://www.ipowatch.in",
}

CHITTORGARH_HEADERS = {
    **DEFAULT_HEADERS,
    "Referer": "https://www.chittorgarh.com/",
    "Origin": "https://www.chittorgarh.com",
}

# Retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ipo", tags=["ipo"])


class IPODataService:
    """Centralized IPO data fetching with multi-source fallback and caching."""

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._session: Optional[httpx.AsyncClient] = None

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = httpx.AsyncClient(
                timeout=httpx.Timeout(20),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                follow_redirects=True,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    def _cache_key(self, prefix: str, *parts: str) -> str:
        return f"ipo:{prefix}:{':'.join(parts)}"

    async def _get_cached(self, key: str) -> Optional[Any]:
        if not self.redis or not self.redis.enabled:
            return None
        try:
            return await self.redis.get_json(key)
        except Exception:
            return None

    async def _set_cached(self, key: str, value: Any, ttl: int) -> bool:
        if not self.redis or not self.redis.enabled:
            return False
        try:
            return await self.redis.set_json(key, value, ttl_seconds=ttl)
        except Exception:
            return False

    async def _fetch_with_retry(
        self,
        url: str,
        headers: Optional[Dict] = None,
        params: Optional[Dict] = None,
        max_retries: int = MAX_RETRIES,
    ) -> Optional[httpx.Response]:
        """Fetch URL with exponential backoff retry."""
        session = await self._get_session()
        h = {**DEFAULT_HEADERS, **(headers or {})}

        for attempt in range(MAX_RETRIES):
            try:
                resp = await session.get(url, headers=h, params=params, timeout=20)
                if resp.status_code == 200:
                    return resp
                elif resp.status_code == 429:
                    # Rate limited - wait longer
                    await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt) * 2)
                    continue
                elif 500 <= resp.status_code < 600:
                    await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                else:
                    # Client error - don't retry
                    logger.warning("HTTP %d for %s", resp.status_code, url)
                    return resp
            except httpx.TimeoutException:
                logger.warning("Timeout fetching %s (attempt %d/%d)", url, attempt + 1, MAX_RETRIES)
            except Exception as e:
                logger.warning("Error fetching %s: %s", url, e)

            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))

        return None

    async def _fetch_json(self, url: str, headers: Optional[Dict] = None) -> Optional[Dict]:
        resp = await self._fetch_with_retry(url, headers=headers)
        if resp and resp.status_code == 200:
            try:
                return resp.json()
            except Exception as e:
                logger.warning("JSON parse failed for %s: %s", url, e)
        return None

    # ===== IPO LIST FETCHING =====

    async def fetch_upcoming_ipos(self) -> Tuple[List[Dict], str]:
        """Fetch upcoming IPOs with multi-source fallback."""
        cache_key = self._cache_key("upcoming", "list")
        cached = await self._get_cached(cache_key)
        if cached:
            return cached, "cached"

        # Try sources in priority order
        for source_id, source_name, url in SOURCE_PRIORITY:
            try:
                if source_id == "nse_official":
                    data = await self._fetch_nse_ipos()
                elif source_id == "bse_official":
                    data = await self._fetch_bse_ipos()
                else:
                    data = await self._fetch_ipowatch_upcoming()

                if data:
                    await self._set_cached(cache_key, data, CACHE_TTL_UPCOMING)
                    return data, source_id
            except Exception as e:
                logger.warning("Failed to fetch from %s: %s", source_id, e)
                continue

        return [], "unavailable"

    async def _fetch_nse_ipos(self) -> List[Dict]:
        """Fetch IPOs from NSE official API."""
        session = await self._get_session()
        resp = await session.get(NSE_IPO_URL, headers=NSE_HEADERS, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        # NSE returns array of IPO objects
        return self._normalize_nse_ipos(data.get("data", data) if isinstance(data, dict) else data)

    def _normalize_nse_ipos(self, items: List[Dict]) -> List[Dict]:
        """Normalize NSE IPO format to our standard format."""
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "companyName": item.get("companyName", item.get("company", "Unknown")),
                "issueType": item.get("issueType", "Public"),
                "issuePrice": item.get("issuePrice", item.get("priceBand", "N/A")),
                "priceBand": item.get("priceBand", "N/A"),
                "lotSize": item.get("lotSize", item.get("marketLot", 0)),
                "openDate": item.get("openDate", item.get("issueOpenDate")),
                "closeDate": item.get("closeDate", item.get("issueCloseDate")),
                "listingDate": item.get("listingDate", item.get("dateOfListing")),
                "exchange": "NSE",
                "source": "NSE Official",
            })
        return normalized

    async def _fetch_bse_ipos(self) -> List[Dict]:
        """Fetch IPOs from BSE official API."""
        session = await self._get_session()
        resp = await session.get(BSE_IPO_URL, headers=BSE_HEADERS, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return self._normalize_bse_ipos(data.get("data", data) if isinstance(data, dict) else data)

    def _normalize_bse_ipos(self, items: List[Dict]) -> List[Dict]:
        """Normalize BSE IPO format to our standard format."""
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "companyName": item.get("companyName", item.get("company", "Unknown")),
                "issueType": item.get("issueType", "Public"),
                "issuePrice": item.get("issuePrice", item.get("priceBand", "N/A")),
                "priceBand": item.get("priceBand", "N/A"),
                "lotSize": item.get("lotSize", item.get("marketLot", 0)),
                "openDate": item.get("openDate", item.get("issueOpenDate")),
                "closeDate": item.get("closeDate", item.get("issueCloseDate")),
                "listingDate": item.get("listingDate", item.get("dateOfListing")),
                "exchange": "BSE",
                "source": "BSE Official",
            })
        return normalized

    async def _fetch_ipowatch_upcoming(self) -> List[Dict]:
        """Fetch from IPOWatch as fallback."""
        data = await self._fetch_json(IPOWATCH_UPCOMING, headers=IPOWATCH_HEADERS)
        if not data:
            return []
        return data.get("ipos", data.get("data", [])) if isinstance(data, dict) else []

    # ===== GMP FETCHING WITH MULTI-SOURCE =====

    async def fetch_gmp_data(self) -> Tuple[List[Dict], str]:
        """Fetch GMP from multiple sources with cross-verification."""
        cache_key = self._cache_key("gmp", "live")
        cached = await self._get_cached(cache_key)
        if cached:
            return cached, "cached"

        all_gmp = {}
        source_status = []

        for source_id, source_name, url in GMP_SOURCES:
            try:
                if source_id == "ipowatch":
                    data = await self._fetch_ipowatch_gmp()
                else:
                    data = await self._fetch_chittorgarh_gmp()

                if data:
                    for item in data:
                        key = self._make_gmp_key(item)
                        if key not in all_gmp:
                            all_gmp[key] = {"sources": {}, "item": item}
                        all_gmp[key]["sources"][source_id] = item
                    source_status.append(source_id)
            except Exception as e:
                logger.warning("GMP fetch failed for %s: %s", source_id, e)

        # Cross-verify: only keep items with >=2 sources or high confidence
        verified_gmp = []
        for key, data in all_gmp.items():
            sources = data["sources"]
            if len(sources) >= 2:
                # Cross-verified - average the GMP
                gmps = [v.get("gmp", v.get("greyMarketPrice", 0)) for v in sources.values() if v.get("gmp") or v.get("greyMarketPrice")]
                avg_gmp = sum(gmps) / len(gmps) if gmps else 0
                item = data["item"].copy()
                item["gmp"] = round(avg_gmp, 2)
                item["gmpVerified"] = True
                item["gmpSources"] = list(sources.keys())
            elif sources:
                # Single source
                item = data["item"].copy()
                item["gmpVerified"] = False
                item["gmpSources"] = list(sources.keys())
            else:
                continue
            verified_gmp.append(item)

        status = "healthy" if verified_gmp else "no_data"
        await self._set_cached(cache_key, verified_gmp, CACHE_TTL_LIVE)
        return verified_gmp, status

    async def _fetch_ipowatch_gmp(self) -> List[Dict]:
        data = await self._fetch_json(IPOWATCH_GMP, headers=IPOWATCH_HEADERS)
        if not data:
            return []
        return data.get("gmpData", data.get("data", [])) if isinstance(data, dict) else []

    async def _fetch_chittorgarh_gmp(self) -> List[Dict]:
        """Fetch GMP from Chittorgarh (HTML parsing needed)."""
        # For now, return empty - would need HTML parsing
        # Could use: https://www.chittorgarh.com/ipo/gmp/
        data = await self._fetch_json(CHITTORGARH_GMP, headers=CHITTORGARH_HEADERS)
        if not data:
            return []
        return data if isinstance(data, list) else []

    def _make_gmp_key(self, item: Dict) -> str:
        """Create unique key for GMP deduplication."""
        name = item.get("companyName", item.get("company", item.get("name", "")))
        return hashlib.md5(name.lower().strip().encode()).hexdigest()[:16]

    # ===== SUBSCRIPTION STATUS =====

    async def fetch_subscription_status(self) -> List[Dict]:
        """Fetch subscription status from multiple sources."""
        cache_key = self._cache_key("subscription", "status")
        cached = await self._get_cached(cache_key)
        if cached:
            return cached

        all_subs = {}
        for source_id, source_name, url in SUBSCRIPTION_SOURCES:
            try:
                if source_id == "ipowatch":
                    data = await self._fetch_ipowatch_subscription()
                else:
                    data = await self._fetch_nse_subscription()

                for sub in data:
                    key = sub.get("companyName", "").strip().lower()
                    if key and key not in all_subs:
                        all_subs[key] = sub
            except Exception as e:
                logger.warning("Subscription fetch failed for %s: %s", source_id, e)

        return list(all_subs.values())

    async def _fetch_ipowatch_subscription(self) -> List[Dict]:
        data = await self._fetch_json(IPOWATCH_SUBSCRIPTION, headers=IPOWATCH_HEADERS)
        if not data:
            return []
        return data.get("subscriptions", data.get("data", [])) if isinstance(data, dict) else []

    async def _fetch_nse_subscription(self) -> List[Dict]:
        """Fetch NSE official subscription data."""
        # NSE has subscription API but requires session
        return []

    # ===== IPO NEWS =====

    async def fetch_ipo_news(self, lang: str = "en") -> List[Dict]:
        """Fetch IPO news with optional translation."""
        cache_key = self._cache_key("news", lang)
        cached = await self._get_cached(cache_key)
        if cached:
            return cached

        items = []
        for source_id, source_name, url in NEWS_SOURCES:
            try:
                params = {"lang": lang} if source_id == "ipowatch" else {}
                headers = IPOWATCH_HEADERS if source_id == "ipowatch" else DEFAULT_HEADERS
                data = await self._fetch_json(url, headers=headers, params={"lang": lang} if source_id == "ipowatch" else None)
                if data:
                    news_items = data.get("news", data.get("data", [])) if isinstance(data, dict) else []
                    for item in news_items:
                        item["newsSource"] = source_id
                        items.append(item)
            except Exception as e:
                logger.warning("News fetch failed for %s: %s", source_id, e)

        # Deduplicate by title similarity
        seen_titles = set()
        unique_items = []
        for item in items:
            title = item.get("title", "").strip().lower()
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_items.append(item)

        await self._set_cached(self._cache_key("news", lang), unique_items, CACHE_TTL_NEWS)
        return unique_items

    # ===== FULL AGGREGATION =====

    async def get_full_feed(self) -> Dict:
        """Get complete IPO feed with all data merged."""
        # Fetch all data in parallel
        ipos_task = self.fetch_upcoming_ipos()
        gmp_task = self.fetch_gmp_data()
        subs_task = self.fetch_subscription_status()

        (ipos, ipo_source), (gmp_data, gmp_status), subs = await asyncio.gather(
            ipos_task, gmp_task, subs_task, return_exceptions=True
        )

        if isinstance(ipos, Exception):
            ipos, ipo_source = [], "error"
        if isinstance(gmp_data, Exception):
            gmp_data, gmp_status = [], "error"
        if isinstance(subs, Exception):
            subs = []

        # Build GMP lookup
        gmp_lookup = {}
        for g in gmp_data:
            key = self._make_gmp_key(g)
            gmp_lookup[key] = g

        # Build subscription lookup
        sub_lookup = {}
        for s in subs:
            key = s.get("companyName", "").strip().lower()
            sub_lookup[key] = s

        # Merge all data
        now = datetime.now()
        formatted_items = []

        for item in ipos:
            try:
                name = item.get("companyName", "Unknown")
                key = hashlib.md5(name.lower().strip().encode()).hexdigest()[:16]

                # GMP data
                gmp_item = gmp_lookup.get(key, {})
                gmp_val = gmp_item.get("gmp", item.get("gmp", 0))

                # Subscription data
                sub_key = item.get("companyName", "").strip().lower()
                sub = sub_lookup.get(sub_key, {})
                sub_pct = sub.get("subscriptionPercent", sub.get("subscription", 0)) or 0
                sub_status = (
                    "Open" if sub_pct > 0 and sub_pct < 100
                    else "Closed" if sub_pct >= 100 else "Not subscribed"
                )

                # Format price band
                band = item.get("issuePrice", item.get("priceBand", item.get("priceBand", "N/A")))
                if isinstance(band, (int, float)):
                    band = f"₹{band:,.0f}"

                formatted_items.append({
                    "name": item.get("companyName", "Unknown"),
                    "type": "mainboard" if item.get("issueType", "").lower() in ("public", "mainboard") else "sme",
                    "gmp": gmp_val if gmp_val else 0,
                    "band": band,
                    "sub": item.get("lotSize", item.get("marketLot", 0)),
                    "gmpHistory": item.get("gmpHistory", []),
                    "source": item.get("source", "Aggregated"),
                    "lastUpdatedAt": datetime.now().isoformat(),
                    "status": sub_status,
                    "subscriptionPct": sub_pct,
                    "gmpVerified": gmp_item.get("gmpVerified", False),
                    "gmpSources": gmp_item.get("gmpSources", []),
                    "openDate": item.get("openDate"),
                    "closeDate": item.get("closeDate"),
                    "listingDate": item.get("listingDate"),
                    "exchange": item.get("exchange"),
                })
            except Exception as e:
                logger.warning("Failed to format IPO item: %s", e)

        # Determine overall GMP status
        gmp_verified_count = sum(1 for g in gmp_data if g.get("gmpVerified"))
        if gmp_status == "healthy" and gmp_verified_count > 0:
            gmp_status = "verified"
        elif gmp_status == "healthy":
            gmp_status = "single_source"

        return {
            "success": True,
            "items": formatted_items[:30],
            "gmpFeed": gmp_status,
            "gmpVerifiedCount": gmp_verified_count,
            "gmpTotalCount": len(gmp_data),
            "ipoSource": ipo_source if not isinstance(ipos, Exception) else "error",
            "message": self._get_status_message(gmp_status),
            "lastUpdated": datetime.now().isoformat(),
        }

    def _get_status_message(self, gmp_status: str) -> str:
        messages = {
            "healthy": "Live GMP data available",
            "verified": "Live GMP data available (multi-source verified)",
            "single_source": "GMP data available (single source)",
            "no_data": "No GMP data available",
            "unavailable": "GMP feed temporarily unavailable",
            "error": "GMP feed error",
            "cached": "Using cached GMP data",
        }
        return messages.get(gmp_status, "GMP status unknown")


# ===== API ENDPOINTS =====

# Global service instance
_ipo_service: Optional[IPODataService] = None


def get_ipo_service(request: Request) -> IPODataService:
    global _ipo_service
    if _ipo_service is None:
        _ipo_service = IPODataService(request.app.state.redis)
    return _ipo_service


@router.get("/feed", response_model=dict)
async def ipo_feed(request: Request) -> dict:
    """Fetch complete IPO feed with live GMP, subscription status."""
    service = get_ipo_service(request)
    return await service.get_full_feed()


@router.get("/gmp", response_model=dict)
async def ipo_gmp(request: Request) -> dict:
    """Get live GMP data only."""
    service = get_ipo_service(request)
    gmp_data, status = await service.fetch_gmp_data()
    return {
        "success": True,
        "items": gmp_data,
        "gmpFeed": status,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/subscription", response_model=dict)
async def ipo_subscription(request: Request) -> dict:
    """Get subscription status for all IPOs."""
    service = get_ipo_service(request)
    subs = await service.fetch_subscription_status()
    return {
        "success": True,
        "items": subs,
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/news_feed", response_model=dict)
async def ipo_news_feed(request: Request, tl: str = Query("en")) -> dict:
    """Fetch IPO news feed in specified language."""
    service = get_ipo_service(request)
    items = await service.fetch_ipo_news(tl)
    return {
        "success": True,
        "items": items,
        "serverTranslated": tl not in ("en", "en-US"),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/health", response_model=dict)
async def ipo_health(request: Request) -> dict:
    """Health check for IPO data sources."""
    service = get_ipo_service(request)
    results = {}

    # Check each source
    checks = [
        ("nse", NSE_IPO_URL),
        ("bse", BSE_IPO_URL),
        ("ipowatch", IPOWATCH_UPCOMING),
        ("gmp", IPOWATCH_GMP),
    ]

    for name, url in checks:
        try:
            session = await service._get_session()
            resp = await session.get(url, headers=DEFAULT_HEADERS, timeout=10)
            results[name] = {"status": "ok" if resp.status_code == 200 else "degraded", "code": resp.status_code}
        except Exception as e:
            results[name] = {"status": "down", "error": str(e)}

    all_ok = all(r.get("status") == "ok" for r in results.values())
    return {
        "success": True,
        "status": "healthy" if all_ok else "degraded",
        "sources": results,
        "timestamp": datetime.now().isoformat(),
    }
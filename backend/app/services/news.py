from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import logging
import math
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPIError
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from ..config import Settings, get_settings
import httpx as _httpx_client

logger = logging.getLogger(__name__)

PRIORITY_TERMS = (
    "war",
    "geopolitic",
    "tariff",
    "federal reserve",
    "fed rate",
    "fed interest",
    "rbi rate",
    "reserve bank of india",
    "interest rate decision",
    "rate hike",
    "rate cut",
    "inflation",
    "crude oil",
    "oil price",
    "fii",
    "foreign institutional investor",
    "dii",
    "domestic institutional investor",
    "institutional flow",
    "whale",
    "crypto crash",
    "crash",
)

RSS_FEEDS = {
    "google_news_rss": (
        "https://news.google.com/rss/search?q="
        + quote_plus(
            '"war" OR tariff OR "Fed rate" OR "RBI rate" OR inflation '
            'OR "crude oil" OR FII OR DII'
        )
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    ),
    "moneycontrol_rss": "https://www.moneycontrol.com/rss/latestnews.xml",
    "economic_times_rss": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "coindesk_rss": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}


def _clean_text(value: Any, limit: int = 500) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _parse_published_at(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    text = str(value or "").strip()
    if text:
        if re.fullmatch(r"\d{8}T\d{6}", text):
            return datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
                return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc)


def priority_matches(text: str) -> tuple[str, ...]:
    lowered = text.casefold()
    matches = []
    for term in PRIORITY_TERMS:
        if term in {"fii", "dii"}:
            found = re.search(rf"\b{term}\b", lowered)
        else:
            found = term in lowered
        if found:
            matches.append(term.upper() if term in {"fii", "dii"} else term)
    return tuple(matches)


def normalize_news_item(
    source: str,
    *,
    title: Any,
    url: Any,
    description: Any = "",
    published_at: Any = None,
    source_name: Any = None,
) -> dict[str, Any] | None:
    clean_title = _clean_text(title, 400)
    clean_url = str(url or "").strip()
    if not clean_title or not clean_url.startswith(("https://", "http://")):
        return None

    published = _parse_published_at(published_at)
    matches = priority_matches(f"{clean_title} {_clean_text(description)}")
    event_id = hashlib.sha256(f"{source}\n{clean_url}".encode()).hexdigest()
    
    # Breaking news: high priority + recent (< 30 min)
    now = datetime.now(timezone.utc)
    is_breaking = bool(matches) and (now - published) < timedelta(minutes=30)
    
    return {
        "id": event_id,
        "title": clean_title,
        "description": _clean_text(description),
        "url": clean_url,
        "pubDate": published.isoformat(),
        "verifiedSource": _clean_text(source_name or source.replace("_", " ").title(), 100),
        "source": source,
        "trustTier": "MEDIA",
        "eventCategory": "MACRO" if matches else "MARKET",
        "priority": bool(matches),
        "priorityReasons": list(matches),
        "breaking": is_breaking,
    }


def _normalize_rows(source: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if source == "newsapi":
            item = normalize_news_item(
                source,
                title=row.get("title"),
                url=row.get("url"),
                description=row.get("description"),
                published_at=row.get("publishedAt"),
                source_name=(row.get("source") or {}).get("name"),
            )
        elif source == "finnhub":
            item = normalize_news_item(
                source,
                title=row.get("headline"),
                url=row.get("url"),
                description=row.get("summary"),
                published_at=row.get("datetime"),
                source_name=row.get("source"),
            )
        elif source == "alpha_vantage":
            item = normalize_news_item(
                source,
                title=row.get("title"),
                url=row.get("url"),
                description=row.get("summary"),
                published_at=row.get("time_published"),
                source_name=row.get("source"),
            )
        elif source == "marketaux":
            item = normalize_news_item(
                source,
                title=row.get("title"),
                url=row.get("url"),
                description=row.get("description"),
                published_at=row.get("published_at"),
                source_name=row.get("source"),
            )
        elif source == "newsdata":
            item = normalize_news_item(
                source,
                title=row.get("title"),
                url=row.get("link"),
                description=row.get("description"),
                published_at=row.get("pubDate"),
                source_name=row.get("source_name"),
            )
        elif source == "coinpaprika":
            item = normalize_news_item(
                source,
                title=row.get("name"),
                url=row.get("link"),
                description=row.get("description"),
                published_at=row.get("date"),
                source_name="Coinpaprika",
            )
        else:
            continue
        if item:
            result.append(item)
    return result


async def _json_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()


async def _newsapi(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    data = await _json_get(
        client,
        "https://newsapi.org/v2/everything",
        params={
            "q": '"war" OR tariff OR "Fed rate" OR "RBI rate" OR inflation OR "crude oil" OR FII OR DII',
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 100,
        },
        headers={"X-Api-Key": settings.newsapi_api_key},
    )
    return _normalize_rows("newsapi", data.get("articles", []))


async def _finnhub(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    data = await _json_get(
        client,
        "https://finnhub.io/api/v1/news",
        params={"category": "general", "token": settings.finnhub_api_key},
    )
    return _normalize_rows("finnhub", data if isinstance(data, list) else [])


async def _alpha_vantage(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    data = await _json_get(
        client,
        "https://www.alphavantage.co/query",
        params={
            "function": "NEWS_SENTIMENT",
            "topics": "financial_markets,economy_monetary,energy_transportation",
            "sort": "LATEST",
            "limit": 100,
            "apikey": settings.alpha_vantage_api_key,
        },
    )
    return _normalize_rows("alpha_vantage", data.get("feed", []))


async def _marketaux(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    data = await _json_get(
        client,
        "https://api.marketaux.com/v1/news/all",
        params={
            "api_token": settings.marketaux_api_key,
            "language": "en",
            "countries": "in,us",
            "limit": 50,
        },
    )
    return _normalize_rows("marketaux", data.get("data", []))


async def _newsdata(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    data = await _json_get(
        client,
        "https://newsdata.io/api/1/latest",
        params={
            "apikey": settings.newsdata_api_key,
            "language": "en",
            "category": "business,top",
            "country": "in",
        },
    )
    return _normalize_rows("newsdata", data.get("results", []))


def _coinpaprika_market_signals(rows: list[dict[str, Any]]) -> list[dict]:
    result = []
    for row in rows:
        quotes = row.get("quotes") or {}
        usd = quotes.get("USD") or {}
        try:
            change = float(usd.get("percent_change_24h"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(change) or abs(change) < 10:
            continue

        name = _clean_text(row.get("name"), 100)
        symbol = _clean_text(row.get("symbol"), 20)
        coin_id = str(row.get("id") or "").strip()
        if not name or not coin_id:
            continue
        title_prefix = "Crypto crash signal" if change <= -15 else "Crypto market surge"
        direction = "down" if change < 0 else "up"
        title = f"{title_prefix}: {name} ({symbol}) {direction} {abs(change):.2f}% in 24h"
        description = (
            f"Coinpaprika reports a {abs(change):.2f}% 24-hour price move "
            f"for {name} ({symbol})."
        )
        item = normalize_news_item(
            "coinpaprika",
            title=title,
            url=f"https://api.coinpaprika.com/v1/tickers/{quote_plus(coin_id)}",
            description=description,
            published_at=row.get("last_updated"),
            source_name="Coinpaprika market signal",
        )
        if item:
            result.append(item)
        if len(result) >= 25:
            break
    return result


async def _coinpaprika(client: httpx.AsyncClient, settings: Settings) -> list[dict]:
    base_url = "https://api.coinpaprika.com/v1"
    tickers, events = await asyncio.gather(
        _json_get(
            client,
            f"{base_url}/tickers",
            params={"quotes": "USD"},
        ),
        _json_get(
            client,
            f"{base_url}/coins/btc-bitcoin/events",
        ),
    )
    return [
        *_normalize_rows(
            "coinpaprika",
            events if isinstance(events, list) else [],
        ),
        *_coinpaprika_market_signals(
            tickers if isinstance(tickers, list) else []
        ),
    ]


def _rss_rows(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    items = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        row = {}
        for child in list(element):
            key = child.tag.rsplit("}", 1)[-1].lower()
            row[key] = child.text or ""
        items.append(row)
    return items


async def _rss(
    client: httpx.AsyncClient,
    source: str,
    url: str,
) -> list[dict]:
    response = await client.get(url)
    response.raise_for_status()
    rows = _rss_rows(response.content)
    result = []
    for row in rows:
        item = normalize_news_item(
            source,
            title=row.get("title"),
            url=row.get("link") or row.get("guid"),
            published_at=row.get("pubdate") or row.get("published"),
        )
        if item:
            result.append(item)
    return result


FetchProvider = Callable[[httpx.AsyncClient], Awaitable[list[dict[str, Any]]]]


class NewsEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        redis: Any = None,
        database: Any = None,
        mongo: Any = None,
        firebase: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.redis = redis
        self.database = database
        self.mongo = mongo
        self.firebase = firebase
        self.transport = transport
        self._lock = asyncio.Lock()
        self._known_ids: set[str] = set()
        self._known_order: deque[str] = deque()
        self._latest: list[dict[str, Any]] = []
        self._source_due: dict[str, float] = {}
        self._source_last_poll: dict[str, str] = {}
        self._source_status: dict[str, dict[str, Any]] = {}
        self._last_poll_at: str | None = None
        self._priority_until = 0.0
        self._priority_events: list[dict[str, Any]] = []
        self._cache_restored = False

    def _provider_specs(self) -> list[tuple[str, int, FetchProvider]]:
        settings = self.settings
        specs: list[tuple[str, int, FetchProvider]] = []
        api_sources = (
            ("newsapi", settings.newsapi_api_key, settings.newsapi_poll_seconds, lambda c: _newsapi(c, settings)),
            ("finnhub", settings.finnhub_api_key, settings.finnhub_poll_seconds, lambda c: _finnhub(c, settings)),
            ("alpha_vantage", settings.alpha_vantage_api_key, settings.alpha_vantage_poll_seconds, lambda c: _alpha_vantage(c, settings)),
            ("marketaux", settings.marketaux_api_key, settings.marketaux_poll_seconds, lambda c: _marketaux(c, settings)),
            ("newsdata", settings.newsdata_api_key, settings.newsdata_poll_seconds, lambda c: _newsdata(c, settings)),
        )
        for name, api_key, interval, fetch in api_sources:
            if api_key:
                specs.append((name, interval, fetch))
            else:
                self._source_status[name] = {"status": "not_configured"}

        specs.append(
            (
                "coinpaprika",
                settings.coinpaprika_poll_seconds,
                lambda client: _coinpaprika(client, settings),
            )
        )

        for name, url in RSS_FEEDS.items():
            if name == "google_news_rss" and not settings.news_google_rss_enabled:
                self._source_status[name] = {"status": "disabled", "reason": "license_review_required"}
                continue
            specs.append(
                (
                    name,
                    settings.rss_poll_seconds,
                    lambda client, n=name, feed=url: _rss(client, n, feed),
                )
            )
        return specs

    @property
    def priority_guard_active(self) -> bool:
        return time.monotonic() < self._priority_until

    def snapshot(self) -> dict[str, Any]:
        remaining = max(0.0, self._priority_until - time.monotonic())
        return {
            "items": list(self._latest),
            "providers": {name: dict(value) for name, value in self._source_status.items()},
            "last_poll_at": self._last_poll_at,
            "polling": self.settings.news_auto_poll_enabled,
            "priority_guard_active": self.priority_guard_active,
            "priority_guard_until": (
                (datetime.now(timezone.utc) + timedelta(seconds=remaining)).isoformat()
                if self.priority_guard_active
                else None
            ),
            "priority_events": list(self._priority_events[:25]),
            "intervals_seconds": {
                name: interval for name, interval, _ in self._provider_specs()
            },
        }

    def summaries_with_impact(self) -> list[dict[str, Any]]:
        """Generate full readable summaries with market impact (bullish/bearish reason) for latest items."""
        result = []
        for item in self._latest[:50]:
            summary = self._generate_summary_with_impact(item)
            result.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "summary": summary,
                "eventCategory": item.get("eventCategory"),
                "priority": item.get("priority"),
                "priorityReasons": item.get("priorityReasons"),
                "source": item.get("source"),
                "verifiedSource": item.get("verifiedSource"),
                "url": item.get("url"),
            })
        return result

    async def _gemini_translate_async(self, text: str, target_lang: str) -> str:
        """Translate text using Gemini Flash API (async). English → target language."""
        if not self.settings.gemini_api_key or target_lang in ("en", "en-US"):
            return text
        try:
            endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
            api_key = self.settings.gemini_api_key
            lang_names = {
                "bn": "Bengali (পাকা ও শুদ্ধ বাংলা)",
                "hi": "Hindi (शुद्ध हिंदी)",
                "gu": "Gujarati (શુદ્ધ ગુજરાતી)",
                "mr": "Marathi (शुद्ध मराठी)",
                "ta": "Tamil (தென்தமிழ்)",
            }
            lang_name = lang_names.get(target_lang, target_lang)
            payload = {
                "contents": [{"role": "user", "parts": [{"text": f"Translate this English financial news into fluent, mature, natural {lang_name}. Preserve financial terminology, numbers, and proper nouns. Do not add any commentary, explanations, or English phrases. Return only the translation.\n\nText: {text}"}]}],
            }
            headers = {"Content-Type": "application/json"}
            url = f"{endpoint}?key={api_key}"
            async with _httpx_client.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=headers, timeout=15)
                if resp.status_code != 200:
                    return text
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return text
                content = candidates[0].get("content", "")
                parts = content.get("parts", [])
                if not parts:
                    return text
                translated = parts[0].get("text", "").strip()
                if not translated:
                    return text
                # Verify script matches target language
                if target_lang == "bn":
                    bn_chars = sum(1 for c in translated if '\u0980' <= c <= '\u09FF')
                    if bn_chars < len(translated) * 0.3:
                        return text
                return translated
        except Exception:
            return text

    async def poll_due(self) -> dict[str, Any]:
        async with self._lock:
            await self._restore_cache()
            specs = self._provider_specs()
            now = time.monotonic()
            due = [
                (name, interval, fetch)
                for name, interval, fetch in specs
                if now >= self._source_due.get(name, 0)
            ]
            if not due:
                return self.snapshot()

            for name, interval, _ in due:
                self._source_due[name] = now + interval
                self._source_last_poll[name] = datetime.now(timezone.utc).isoformat()

            timeout = httpx.Timeout(self.settings.news_provider_timeout_seconds)
            limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
                headers={"User-Agent": "ARAF-Intelligence-News/1.0"},
                transport=self.transport,
            ) as client:
                responses = await asyncio.gather(
                    *(fetch(client) for _, _, fetch in due),
                    return_exceptions=True,
                )

            discovered: list[dict[str, Any]] = []
            for (name, _, _), response in zip(due, responses):
                if isinstance(response, asyncio.CancelledError):
                    raise response
                if isinstance(response, BaseException):
                    status_code = (
                        response.response.status_code
                        if isinstance(response, httpx.HTTPStatusError)
                        else None
                    )
                    self._source_status[name] = {
                        "status": "error",
                        "error": type(response).__name__,
                        "http_status": status_code,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    logger.warning(
                        "News source %s request failed (%s, HTTP %s)",
                        name,
                        type(response).__name__,
                        status_code or "n/a",
                    )
                    continue
                newest_at = max(
                    (_parse_published_at(item["pubDate"]) for item in response),
                    default=None,
                )
                age_seconds = (
                    max(0, int((datetime.now(timezone.utc) - newest_at).total_seconds()))
                    if newest_at
                    else None
                )
                self._source_status[name] = {
                    "status": "stale" if age_seconds is not None and age_seconds > 1800 else "healthy",
                    "count": len(response),
                    "latest_age_seconds": age_seconds,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                discovered.extend(response)

            fresh_items = []
            for item in discovered:
                item_id = item["id"]
                if item_id in self._known_ids:
                    continue
                self._known_ids.add(item_id)
                self._known_order.append(item_id)
                fresh_items.append(item)
            while len(self._known_order) > 20000:
                self._known_ids.discard(self._known_order.popleft())

            # Generate summaries with market impact for fresh items
            for item in fresh_items:
                item["summary"] = self._generate_summary_with_impact(item)
            
            # Translate fresh items to all supported languages
            if self.settings.gemini_api_key:
                await self._translate_fresh_items(fresh_items)

            self._latest = sorted(
                [*fresh_items, *self._latest],
                key=lambda item: (not item.get("breaking", False), item["pubDate"]),
                reverse=True,
            )[:300]
            self._last_poll_at = datetime.now(timezone.utc).isoformat()
            await self._persist_updates(fresh_items)
            return self.snapshot()

    async def _restore_cache(self) -> None:
        if self._cache_restored:
            return
        self._cache_restored = True
        if not self.redis or not self.redis.enabled:
            return
        try:
            cached = await self.redis.get_json("news:latest")
            if isinstance(cached, dict) and isinstance(cached.get("items"), list):
                self._latest = [
                    item
                    for item in cached["items"]
                    if isinstance(item, dict) and item.get("id")
                ][:300]
                for item in self._latest:
                    self._known_ids.add(item["id"])
                    self._known_order.append(item["id"])
            metadata = await self.redis.get_json("news:metadata")
            if isinstance(metadata, dict):
                self._last_poll_at = metadata.get("last_poll_at")
                poll_times = metadata.get("source_last_poll", {})
                if isinstance(poll_times, dict):
                    now_wall = datetime.now(timezone.utc)
                    now_mono = time.monotonic()
                    intervals = {
                        name: interval for name, interval, _ in self._provider_specs()
                    }
                    for name, value in poll_times.items():
                        if name not in intervals:
                            continue
                        try:
                            previous = _parse_published_at(value)
                            elapsed = max(0, (now_wall - previous).total_seconds())
                            self._source_last_poll[name] = previous.isoformat()
                            self._source_due[name] = now_mono + max(
                                0, intervals[name] - elapsed
                            )
                        except (TypeError, ValueError, OverflowError):
                            continue
            priority = await self.redis.get_json("news:priority_guard")
            if isinstance(priority, dict) and priority.get("until"):
                expires = _parse_published_at(priority["until"])
                remaining = (expires - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    self._priority_until = time.monotonic() + min(
                        remaining, self.settings.news_priority_lock_seconds
                    )
        except (RedisError, ValueError):
            logger.exception("Could not restore cached news state")

    async def _persist_updates(self, fresh_items: list[dict[str, Any]]) -> None:
        if self.redis and self.redis.enabled:
            try:
                await self.redis.set_json("news:latest", {"items": self._latest}, ttl_seconds=900)
                await self.redis.set_json(
                    "news:metadata",
                    {
                        "last_poll_at": self._last_poll_at,
                        "source_last_poll": self._source_last_poll,
                    },
                    ttl_seconds=86400,
                )
            except (RedisError, ValueError):
                logger.exception("Could not cache the latest normalized news feed")

        if fresh_items and self.mongo and self.mongo.enabled:
            try:
                await self.mongo.insert_news_items(fresh_items)
            except PyMongoError:
                logger.exception("Could not persist news history to MongoDB")

        now = datetime.now(timezone.utc)
        urgent = [
            item
            for item in fresh_items
            if item["priority"]
            and _parse_published_at(item["pubDate"]) >= now - timedelta(minutes=30)
        ]
        if not urgent:
            return

        self._priority_until = max(
            self._priority_until,
            time.monotonic() + self.settings.news_priority_lock_seconds,
        )
        if self.redis and self.redis.enabled:
            try:
                await self.redis.set_json(
                    "news:priority_guard",
                    {
                        "until": (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=self.settings.news_priority_lock_seconds)
                        ).isoformat()
                    },
                    ttl_seconds=self.settings.news_priority_lock_seconds,
                )
            except (RedisError, ValueError):
                logger.exception("Could not persist the priority-news guard state")
        self._priority_events = [*urgent, *self._priority_events][:25]
        for item in urgent:
            if self.database and self.database.enabled:
                try:
                    await self.database.add_audit_event(
                        actor_id="news-engine",
                        event_type="news.priority_detected",
                        resource_type="news_item",
                        resource_id=item["id"],
                        action="priority_override",
                        outcome="success",
                        payload={
                            "source": item["source"],
                            "title": item["title"],
                            "url": item["url"],
                            "reasons": item["priorityReasons"],
                        },
                    )
                except (SQLAlchemyError, RuntimeError):
                    logger.exception("Could not audit a priority news event")
            if self.firebase and self.firebase.enabled:
                try:
                    await self.firebase.publish_market_alert(item)
                except (FirebaseError, GoogleAPIError, RuntimeError):
                    logger.exception("Could not publish a priority news event to Firebase")

    def _generate_summary_with_impact(self, item: dict[str, Any]) -> str:
        """Generate a full readable summary with market impact (bullish/bearish reason) using LLM."""
        title = item.get("title", "") or ""
        description = item.get("description", "") or ""
        category = item.get("eventCategory", "MARKET") or "MARKET"
        reasons = item.get("priorityReasons", []) or []

        clean_title = html.unescape(title).strip()
        clean_desc = html.unescape(description).strip()

        if not clean_title:
            return "No summary available."

        # Build context-aware summary with LLM if available
        if self.settings.gemini_api_key:
            llm_summary = self._llm_generate_impact(clean_title, clean_desc, category, reasons)
            if llm_summary:
                return llm_summary

        # Fallback: keyword-based (existing logic)
        summary_parts = [clean_title]
        if clean_desc:
            summary_parts.append(clean_desc)

        impact_parts = []
        if "FII" in reasons or "DII" in reasons:
            impact_parts.append(" institutional flow activity")
        if "crude oil" in reasons or "oil price" in reasons:
            impact_parts.append(" crude oil price movement")
        if "inflation" in reasons:
            impact_parts.append(" inflation data")
        if "rate hike" in reasons or "rate cut" in reasons:
            impact_parts.append(" interest rate decision")
        if "war" in reasons or "geopolitic" in reasons:
            impact_parts.append(" geopolitical development")

        if impact_parts:
            summary_parts.append("Market impact:" + "".join(impact_parts))

        # Keyword-based bullish/bearish fallback
        bullish_keywords = ["rise", "gain", "up", "increase", "positive", "bull", "upgrade", "surge", "rally", "boost"]
        bearish_keywords = ["fall", "drop", "down", "decrease", "negative", "bear", "downgrade", "crash", "slump", "decline"]
        low_title = clean_title.lower()
        low_desc = clean_desc.lower()

        has_bullish = any(kw in low_title or kw in low_desc for kw in bullish_keywords)
        has_bearish = any(kw in low_title or kw in low_desc for kw in bearish_keywords)

        if has_bullish and has_bearish:
            impact_label = " mixed signals"
        elif has_bullish:
            impact_label = " (bullish)"
        elif has_bearish:
            impact_label = " (bearish)"
        else:
            impact_label = ""

        if impact_label:
            summary_parts.append(f"Trend:{impact_label}")

        summary = ". ".join(summary_parts)
        return summary if summary else clean_title

    def _llm_generate_impact(self, title: str, description: str, category: str, reasons: list) -> str | None:
        """Generate market impact summary using Gemini LLM."""
        try:
            endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
            api_key = self.settings.gemini_api_key
            prompt = f"""Analyze this financial news for Indian market impact. Provide a concise 3-line response:

NEWS: {title}. {description}
CATEGORY: {category}
PRIORITY SIGNALS: {', '.join(reasons) if reasons else 'None'}

FORMAT (exactly 3 lines):
1. SUMMARY: One-sentence summary of the news.
2. IMPACT: BULLISH | BEARISH | NEUTRAL | MIXED
3. REASON: One-line specific reason (e.g., "FII inflows boost liquidity", "Crude spike hurts importers", "Rate hike fears pressure banks").

No extra text, no formatting, no markdown."""
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            }
            headers = {"Content-Type": "application/json"}
            url = f"{endpoint}?key={api_key}"
            # Use synchronous call for simplicity in this method
            import httpx
            with httpx.Client() as client:
                resp = client.post(url, json=payload, headers=headers, timeout=10)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return None
                content = candidates[0].get("content", "")
                parts = content.get("parts", [])
                if not parts:
                    return None
                llm_response = parts[0].get("text", "").strip()
                if not llm_response:
                    return None
                # Parse the 3-line response
                lines = [ln.strip() for ln in llm_response.split("\n") if ln.strip()]
                if len(lines) >= 3:
                    summary_line = lines[0].replace("SUMMARY:", "").strip()
                    impact_line = lines[1].replace("IMPACT:", "").strip()
                    reason_line = lines[2].replace("REASON:", "").strip()
                    return f"{summary_line}. Impact: {impact_line}. Reason: {reason_line}."
        except Exception:
            return None

    async def _translate_fresh_items(self, items: list[dict[str, Any]]) -> None:
        """Translate fresh items' titles, descriptions, and summaries to supported languages."""
        if not self.settings.gemini_api_key:
            return
        target_langs = ["bn", "hi", "gu", "mr", "ta"]
        for item in items:
            title = item.get("title", "")
            description = item.get("description", "")
            summary = item.get("summary", "")
            if not title:
                continue
            for lang in target_langs:
                try:
                    # Translate title
                    translated_title = await self._gemini_translate_async(title, lang)
                    if translated_title and translated_title != title:
                        item[f"title_{lang}"] = translated_title
                    # Translate description
                    if description:
                        translated_desc = await self._gemini_translate_async(description, lang)
                        if translated_desc and translated_desc != description:
                            item[f"description_{lang}"] = translated_desc
                    # Translate summary
                    if summary:
                        translated_summary = await self._gemini_translate_async(summary, lang)
                        if translated_summary and translated_summary != summary:
                            item[f"summary_{lang}"] = translated_summary
                except Exception:
                    # Continue with other languages/items on failure
                    continue


from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from ..config import get_settings


class MongoHub:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = (
            AsyncIOMotorClient(settings.mongodb_atlas_url, serverSelectionTimeoutMS=5000)
            if settings.mongodb_atlas_url
            else None
        )
        self.database = self.client[settings.mongodb_database] if self.client else None

    @property
    def enabled(self) -> bool:
        return self.database is not None

    async def ping(self) -> bool:
        if self.database is None:
            return False
        await self.database.command("ping")
        return True

    async def ensure_indexes(self) -> None:
        if self.database is None:
            return
        await self.database["market_quotes"].create_index(
            [("subject", 1), ("symbol", 1), ("created_at", -1)],
            name="quote_subject_symbol_time",
        )
        await self.database["scanner_history"].create_index(
            [("subject", 1), ("created_at", -1)],
            name="scanner_subject_time",
        )
        await self.database["position_snapshots"].create_index(
            [("subject", 1), ("broker", 1), ("created_at", -1)],
            name="positions_subject_broker_time",
        )
        await self.database["audit_events"].create_index(
            [("subject", 1), ("created_at", -1)],
            name="audit_subject_time",
        )
        await self.database["news_events"].create_index(
            [("event_id", 1)],
            unique=True,
            name="news_event_id",
        )

    async def insert(self, collection: str, document: dict[str, Any]) -> str | None:
        if self.database is None:
            return None
        record = dict(document)
        record.setdefault("created_at", datetime.now(timezone.utc))
        result = await self.database[collection].insert_one(record)
        return str(result.inserted_id)

    async def insert_news_items(self, items: list[dict[str, Any]]) -> None:
        if self.database is None or not items:
            return
        operations = [
            UpdateOne(
                {"event_id": item["id"]},
                {
                    "$setOnInsert": {
                        "event_id": item["id"],
                        "source": item["source"],
                        "title": item["title"],
                        "description": item["description"],
                        "url": item["url"],
                        "published_at": item["pubDate"],
                        "priority": item["priority"],
                        "priority_reasons": item["priorityReasons"],
                        "created_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            for item in items
        ]
        await self.database["news_events"].bulk_write(operations, ordered=False)

    async def find(self, collection: str, query: dict[str, Any], limit: int = 100) -> list[dict]:
        if self.database is None:
            raise RuntimeError("MongoDB Atlas is not configured")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return await self.database[collection].find(query).sort("_id", -1).limit(limit).to_list(length=limit)

    def close(self) -> None:
        if self.client:
            self.client.close()

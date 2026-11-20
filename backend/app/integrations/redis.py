from __future__ import annotations

import json

from redis.asyncio import Redis

from ..config import get_settings


class RedisCache:
    def __init__(self) -> None:
        settings = get_settings()
        self.redis: Redis | None = (
            Redis.from_url(settings.upstash_redis_url, decode_responses=True)
            if settings.upstash_redis_url
            else None
        )

    @property
    def enabled(self) -> bool:
        return self.redis is not None

    async def ping(self) -> bool:
        return bool(await self.redis.ping()) if self.redis else False

    async def get(self, key: str) -> str | None:
        return await self.redis.get(key) if self.redis else None

    async def set(self, key: str, value: str, ttl_seconds: int = 60) -> None:
        if self.redis:
            await self.redis.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        if self.redis:
            await self.redis.delete(key)

    async def get_json(self, key: str) -> dict | list | None:
        value = await self.get(key)
        if value is None:
            return None
        decoded = json.loads(value)
        if not isinstance(decoded, (dict, list)):
            raise ValueError(f"Cached value for {key} is not a JSON object or array")
        return decoded

    async def set_json(self, key: str, value: dict | list, ttl_seconds: int = 60) -> None:
        await self.set(key, json.dumps(value, separators=(",", ":"), allow_nan=False), ttl_seconds)

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()

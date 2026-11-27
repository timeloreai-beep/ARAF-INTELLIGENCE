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

    async def set_if_absent(
        self, key: str, value: str, ttl_seconds: int
    ) -> bool:
        if not self.redis:
            return False
        return bool(await self.redis.set(key, value, ex=ttl_seconds, nx=True))

    async def increment_with_expiry(self, key: str, ttl_seconds: int) -> int:
        if not self.redis:
            raise RuntimeError("Redis is not configured")
        script = (
            "local n = redis.call('INCR', KEYS[1]); "
            "if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; "
            "return n"
        )
        return int(await self.redis.eval(script, 1, key, ttl_seconds))

    async def delete(self, key: str) -> None:
        if self.redis:
            await self.redis.delete(key)

    async def compare_and_delete(self, key: str, expected_value: str) -> bool:
        if not self.redis:
            return False
        script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]); else return 0; end"
        )
        return bool(await self.redis.eval(script, 1, key, expected_value))

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

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete all Redis keys associated with a user."""
        if not self.redis:
            return 0
        deleted = 0
        patterns = [
            f"session:{user_id}*",
            f"auth:pin:attempts:{user_id}*",
            f"auth:otp:{user_id}*",
            f"auth:revoked-token:{user_id}*",
            f"broker:session:shoonya:{user_id}*",
            f"broker:session:coindcx:{user_id}*",
        ]
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
                if keys:
                    deleted += await self.redis.delete(*keys)
                if cursor == 0:
                    break
        return deleted

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()

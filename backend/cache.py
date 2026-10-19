import asyncio
import time


class MemoryCache:
    """Bounded local fallback with the same get/set shape as redis.asyncio."""
    def __init__(self, capacity=256):
        self.entries = {}
        self.capacity = capacity

    async def get(self, key):
        entry = self.entries.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        self.entries.pop(key, None)
        return None

    async def set(self, key, value, ex):
        if key not in self.entries and len(self.entries) >= self.capacity:
            self.entries.pop(next(iter(self.entries)))
        self.entries[key] = (time.monotonic() + ex, value)


class ResilientCache:
    """Redis outage never prevents source fetching; bounded in-process fallback."""
    def __init__(self, redis_url=None):
        self.local = MemoryCache()
        self.redis = None
        if redis_url:
            from redis.asyncio import Redis
            self.redis = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)

    async def get(self, key):
        if self.redis:
            try:
                value = await asyncio.wait_for(self.redis.get(key), timeout=2)
                if value is not None:
                    return value
            except (OSError, TimeoutError):
                pass
            except Exception as exc:
                from redis.exceptions import RedisError
                if not isinstance(exc, RedisError):
                    raise
        return await self.local.get(key)

    async def set(self, key, value, ex):
        await self.local.set(key, value, ex)
        if self.redis:
            try:
                await asyncio.wait_for(self.redis.set(key, value, ex=ex), timeout=2)
            except (OSError, TimeoutError):
                pass
            except Exception as exc:
                from redis.exceptions import RedisError
                if not isinstance(exc, RedisError):
                    raise

    async def close(self):
        if self.redis:
            await self.redis.aclose()

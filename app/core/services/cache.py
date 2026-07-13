import json
from functools import lru_cache
from typing import Optional, Any
from redis.asyncio import from_url, Redis
from redis.asyncio.client import PubSub
from app.core.config import settings


class CacheService:
    """Service to wrap Redis operations, providing key-value and JSON caching."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(CacheService, cls).__new__(cls)
        return cls._instance

    def __init__(self, redis_url: str):
        """Initializes the Redis async client.

        Args:
            redis_url: Connection URL for Redis (e.g. redis://localhost:6379/0)
        """
        if hasattr(self, "_initialized") and self._initialized:
            return
        self.client: Redis = from_url(
            redis_url,
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30
        )
        self._initialized = True

    async def get(self, key: str) -> Optional[str]:
        """Gets string value for a key from cache.

        Args:
            key: Cache key.

        Returns:
            Optional[str]: Cached string value or None if not found.
        """
        val = await self.client.get(key)
        if val is None:
            return None
        return val.decode("utf-8") if isinstance(val, bytes) else val

    async def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """Sets string value for a key in cache with optional TTL.

        Args:
            key: Cache key.
            value: String value.
            expire: Optional expiration time in seconds (TTL).

        Returns:
            bool: True if set succeeded.
        """
        return bool(await self.client.set(key, value, ex=expire))


    async def delete(self, key: str) -> bool:
        """Deletes a key from cache.

        Args:
            key: Cache key.

        Returns:
            bool: True if key was deleted, False otherwise.
        """
        result = await self.client.delete(key)
        return result > 0

    async def exists(self, key: str) -> bool:
        """Checks if a key exists in cache.

        Args:
            key: Cache key.

        Returns:
            bool: True if key exists, False otherwise.
        """
        result = await self.client.exists(key)
        return result > 0

    async def get_json(self, key: str) -> Optional[Any]:
        """Gets and parses JSON value for a key from cache.

        Args:
            key: Cache key.

        Returns:
            Optional[Any]: Parsed JSON data or None if not found/invalid.
        """
        value = await self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    async def set_json(self, key: str, value: Any, expire: Optional[int] = None) -> bool:
        """Serializes and sets JSON value for a key in cache with optional TTL.

        Args:
            key: Cache key.
            value: Data to serialize.
            expire: Optional expiration time in seconds (TTL).

        Returns:
            bool: True if set succeeded.
        """
        try:
            serialized_value = json.dumps(value)
            return await self.set(key, serialized_value, expire=expire)
        except (TypeError, ValueError):
            return False

    async def flush_all(self) -> bool:
        """Clears all keys in the current database.

        Returns:
            bool: True if database was flushed.
        """
        return await self.client.flushdb()

    async def close(self) -> None:
        """Closes the Redis connection pool."""
        await self.client.close()

    async def publish(self, channel: str, message: str) -> int:
        """Publishes a message to a channel.

        Args:
            channel: Channel name.
            message: Message payload.

        Returns:
            int: Number of subscribers that received the message.
        """
        return await self.client.publish(channel, message)

    def pubsub(self) -> PubSub:
        """Returns a Redis PubSub instance to subscribe to channels.

        Returns:
            PubSub: A PubSub instance.
        """
        return self.client.pubsub()


@lru_cache()
def get_cache_service() -> CacheService:
    """Dependency provider function for CacheService, cached to ensure a singleton instance."""
    return CacheService(settings.REDIS_URL)



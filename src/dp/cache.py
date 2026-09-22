"""Response cache operations."""

from .settings import settings


async def clear_cache() -> None:
    """Flush the response cache database."""
    async with settings.redis(db=settings.FALLBACK_CACHE_REDIS_DB) as redis:
        await redis.flushdb()  # pyright: ignore[reportUnknownMemberType]

"""Response cache operations."""

from .settings import settings


async def clear_response_cache(db: int = 1) -> None:
    """Flush the response cache database."""
    async with settings.redis(db=db) as redis:
        await redis.flushdb()  # pyright: ignore[reportUnknownMemberType]

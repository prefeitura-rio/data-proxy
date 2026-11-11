"""Response cache operations."""

from typing import TYPE_CHECKING

from .constants import CACHE_GENERATION_PREFIX
from .settings import settings

if TYPE_CHECKING:
    from collections.abc import Iterable


async def bump_cache_generations(schemas: Iterable[str]) -> None:
    """Give the cached answers of each schema a new key, and leave the answers of the other schemas alone.

    The proxy puts the counter of the schema of a request in the key of the answer. A request without a profile
    reads the counter of the empty name, which belongs to the default schema, so every run counts it up too.
    """
    async with settings.redis(settings.PROXY_CACHE_REDIS_DB) as redis:
        for name in ("", *sorted(set(schemas))):
            await redis.incr(f"{CACHE_GENERATION_PREFIX}{name}")

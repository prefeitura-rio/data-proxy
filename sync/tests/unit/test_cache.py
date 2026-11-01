"""Tests for the response cache that the sync run clears."""

import pytest

from sync.cache import clear_cache
from tests.helpers.dbos import stub_cache_redis


class TestClearCache:
    """The sync run flushes the cache database of the proxy and no other database."""

    @pytest.mark.parametrize("cache_db", [1, 5, 15])
    async def test_flushes_only_the_cache_database_and_closes_the_client(
        self, monkeypatch: pytest.MonkeyPatch, cache_db: int
    ) -> None:
        """Opens the database of PROXY_CACHE_REDIS_DB, flushes it once and closes it."""
        events = stub_cache_redis(monkeypatch, cache_db)

        await clear_cache()

        assert events == [f"open:{cache_db}", f"flush:{cache_db}", f"close:{cache_db}"]

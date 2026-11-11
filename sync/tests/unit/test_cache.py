"""Tests for the response cache that the sync run invalidates."""

import pytest

from sync.cache import bump_cache_generations
from tests.helpers.dbos import stub_cache_redis


class TestBumpCacheGenerations:
    """A run gives new keys to the answers of its schemas, in the cache database of the proxy and no other database."""

    @pytest.mark.parametrize("cache_db", [1, 5, 15])
    async def test_counts_up_one_key_for_each_schema_in_the_cache_database(
        self, monkeypatch: pytest.MonkeyPatch, cache_db: int
    ) -> None:
        """Open the database of PROXY_CACHE_REDIS_DB, count up the keys once each, and close the client."""
        events = stub_cache_redis(monkeypatch, cache_db)

        await bump_cache_generations(["sales", "hr"])

        assert events == [
            f"open:{cache_db}",
            f"incr:{cache_db}:cache:generation:",
            f"incr:{cache_db}:cache:generation:hr",
            f"incr:{cache_db}:cache:generation:sales",
            f"close:{cache_db}",
        ]

    async def test_counts_up_a_schema_once_when_two_plans_name_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key of a schema moves one step for each run."""
        events = stub_cache_redis(monkeypatch, 1)

        await bump_cache_generations(["sales", "sales"])

        assert events.count("incr:1:cache:generation:sales") == 1

    async def test_counts_up_the_key_of_a_request_without_a_profile_even_with_no_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default schema has the key with the empty name."""
        events = stub_cache_redis(monkeypatch, 1)

        await bump_cache_generations([])

        assert "incr:1:cache:generation:" in events

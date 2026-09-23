"""Integration steps for response cache invalidation."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pydantic.networks import RedisDsn
from pytest_bdd import given, then, when
from redis import Redis
from testcontainers.core.container import DockerContainer

from data_proxy.cache import clear_cache
from data_proxy.settings import settings


@dataclass
class CacheScenario:
    """State for one cache invalidation scenario."""

    client: Redis
    key: str


@pytest.fixture(scope="session")
def valkey() -> Iterator[tuple[str, int]]:
    """Provide a real Valkey endpoint for cache integration tests."""
    container = DockerContainer("valkey/valkey:8-alpine").with_exposed_ports(6379)
    container.start()
    try:
        yield container.get_container_host_ip(), int(container.get_exposed_port(6379))
    finally:
        container.stop()


@given("a real response cache", target_fixture="cache_context")
def real_response_cache(
    valkey: tuple[str, int], monkeypatch: pytest.MonkeyPatch
) -> CacheScenario:
    """Store one entry in a real response cache database."""
    host, port = valkey
    monkeypatch.setattr(settings, "REDIS_WRITE", RedisDsn(f"redis://{host}:{port}/1"))
    client = Redis(host=host, port=port, db=1)
    key = "data-proxy-test-cache"
    client.set(key, b"value")
    return CacheScenario(client=client, key=key)


@when("I clear the response cache")
def clear_response_cache() -> None:
    """Clear the configured response cache."""
    asyncio.run(clear_cache())


@then("the response cache is empty")
def response_cache_is_empty(cache_context: CacheScenario) -> None:
    """Verify that the stored response is gone."""
    assert cache_context.client.get(cache_context.key) is None
    cache_context.client.close()

"""Tests for application settings."""

import pytest
from redis.asyncio import Redis

from data_proxy.models import SchemaWriters
from data_proxy.settings import Settings


def test_redis_uses_write_url_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_READ", "redis://reader:6379/1")
    monkeypatch.setenv("REDIS_WRITE", "redis://writer:6379/0")
    settings = Settings(
        SCHEMA_WRITERS=SchemaWriters(writers={"test": "postgresql://test"})
    )

    client = settings.redis()

    assert isinstance(client, Redis)
    assert client.connection_pool.connection_kwargs["host"] == "writer"
    assert client.connection_pool.connection_kwargs["db"] == 0


def test_redis_selects_read_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_READ", "redis://reader:6379/1")
    monkeypatch.setenv("REDIS_WRITE", "redis://writer:6379/0")
    settings = Settings(
        SCHEMA_WRITERS=SchemaWriters(writers={"test": "postgresql://test"})
    )

    client = settings.redis(role="read")

    assert client.connection_pool.connection_kwargs["host"] == "reader"
    assert client.connection_pool.connection_kwargs["db"] == 1

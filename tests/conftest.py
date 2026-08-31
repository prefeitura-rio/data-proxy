"""Shared fixtures for the data-proxy test suite."""

from collections.abc import Callable
from pathlib import Path

import pytest

from dp.models import SchemaWriters, SyncConfig
from dp.settings import Settings, settings
from tests.helpers import FakeDuckDBConnection, FakePgConn, FakeRedis


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings at an empty, temporary sync configuration file."""
    path = tmp_path / "sync.json"
    path.write_text('{"schemas": {}}')
    writers_path = tmp_path / "writers.json"
    writers_path.write_text('{"writers": {}}')
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", path)
    monkeypatch.setattr(settings, "SCHEMA_WRITERS_FILE", writers_path)
    return path


@pytest.fixture
def config(path: Path) -> Callable[[SyncConfig], None]:
    """Write one typed synchronization configuration."""

    def write(value: SyncConfig) -> None:
        path.write_text(value.model_dump_json())

    return write


@pytest.fixture
def redis() -> FakeRedis:
    """Return isolated in-memory Redis state."""
    return FakeRedis()


@pytest.fixture
def valkey(redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Configure settings to return the isolated Redis state."""

    def make_redis(_: Settings) -> FakeRedis:
        return redis

    monkeypatch.setattr(Settings, "make_redis", make_redis)
    return redis


@pytest.fixture
def writers() -> SchemaWriters:
    """Return writer DSNs for common test schemas."""
    return SchemaWriters(
        writers={"app": "postgresql://writer", "other": "postgresql://writer"}
    )


@pytest.fixture
def postgres() -> FakePgConn:
    """Return an isolated PostgreSQL connection double."""
    return FakePgConn()


@pytest.fixture
def duckdb() -> FakeDuckDBConnection:
    """Return an isolated DuckDB connection double."""
    return FakeDuckDBConnection()

"""Tests for replication readiness."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from psycopg import AsyncConnection

from dp.replication import current_wal_lsn, wait_for_replica_replay
from dp.settings import settings
from tests.fixtures.types import Postgres


@pytest.mark.asyncio
async def test_current_wal_lsn(postgres: Postgres) -> None:
    lsn = await current_wal_lsn(postgres.connection)

    assert "/" in lsn


@pytest.mark.asyncio
async def test_no_replicas_are_ready(postgres: Postgres) -> None:
    await wait_for_replica_replay(postgres.connection, "0/1")


@pytest.mark.asyncio
async def test_replica_catches_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REPLICATION_WAIT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(settings, "REPLICATION_POLL_INTERVAL_SECONDS", 0)
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(side_effect=[(False,), (True,)])
    monkeypatch.setattr("dp.replication.execute_sql", AsyncMock(return_value=cursor))

    await wait_for_replica_replay(MagicMock(spec=AsyncConnection), "0/1")


@pytest.mark.asyncio
async def test_replica_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REPLICATION_WAIT_TIMEOUT_SECONDS", 0)
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=(False,))
    monkeypatch.setattr("dp.replication.execute_sql", AsyncMock(return_value=cursor))

    with pytest.raises(TimeoutError):
        await wait_for_replica_replay(MagicMock(spec=AsyncConnection), "0/1")


@pytest.mark.asyncio
async def test_missing_wal_lsn(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=(None,))
    monkeypatch.setattr("dp.replication.execute_sql", AsyncMock(return_value=cursor))

    with pytest.raises(RuntimeError):
        await current_wal_lsn(MagicMock(spec=AsyncConnection))

"""Tests for PostgreSQL replication readiness."""

from unittest.mock import AsyncMock

import pytest

import data_proxy.replication as replication


class TestCurrentWalLsn:
    """CurrentWalLsn behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_lsn_from_postgres(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the WAL position from a valid row."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = ("0/123",)
        monkeypatch.setattr(replication, "execute_sql", AsyncMock(return_value=cursor))
        assert await replication.current_wal_lsn(AsyncMock()) == "0/123"

    @pytest.mark.asyncio
    async def test_rejects_invalid_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a result that does not contain a string LSN."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (None,)
        monkeypatch.setattr(replication, "execute_sql", AsyncMock(return_value=cursor))
        with pytest.raises(RuntimeError, match="WAL position"):
            await replication.current_wal_lsn(AsyncMock())


class TestReplicasReplayed:
    """ReplicasReplayed behavior tests."""

    @pytest.mark.asyncio
    async def test_accepts_caught_up_replicas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Complete when PostgreSQL reports caught-up replicas."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (True,)
        monkeypatch.setattr(replication, "execute_sql", AsyncMock(return_value=cursor))
        await replication.replicas_replayed(AsyncMock(), "0/123")

    @pytest.mark.asyncio
    async def test_rejects_lagging_replicas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a false replay result."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (False,)
        monkeypatch.setattr(replication, "execute_sql", AsyncMock(return_value=cursor))
        with pytest.raises(RuntimeError, match="still behind"):
            await replication.replicas_replayed(AsyncMock(), "0/123")

    @pytest.mark.asyncio
    async def test_rejects_null_replicas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reject a null replay result."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (None,)
        monkeypatch.setattr(replication, "execute_sql", AsyncMock(return_value=cursor))
        with pytest.raises(RuntimeError, match="still behind"):
            await replication.replicas_replayed(AsyncMock(), "0/123")

    @pytest.mark.asyncio
    async def test_rejects_missing_replay_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a missing replay result."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = None
        monkeypatch.setattr(replication, "execute_sql", AsyncMock(return_value=cursor))
        with pytest.raises(RuntimeError, match="still behind"):
            await replication.replicas_replayed(AsyncMock(), "0/123")

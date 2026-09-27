"""Unit tests for asynchronous transaction and readiness helpers."""

from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection

from data_proxy.postgres import Postgres
from data_proxy.utils import wait_for


class TestWaitFor:
    """Readiness wait behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_when_check_succeeds(self) -> None:
        """Return after the readiness check succeeds."""
        attempts = 0

        async def check() -> None:
            nonlocal attempts
            attempts += 1

        await wait_for(check, timeout=0, interval=0, message="timed out")
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_translates_check_failure_to_timeout(self) -> None:
        """Translate a failed readiness check into a timeout."""
        attempts = 0

        async def check() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("not ready")

        with pytest.raises(TimeoutError, match="timed out"):
            await wait_for(check, timeout=0, interval=0, message="timed out")
        assert attempts == 1


class TestAtomic:
    """Atomic transaction behavior tests."""

    @pytest.mark.asyncio
    async def test_commits_after_success(self) -> None:
        """Commit the connection after a successful block."""
        connection = AsyncMock(spec=AsyncConnection)

        async with Postgres(connection=connection).atomic():
            pass

        connection.commit.assert_awaited_once()
        connection.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rolls_back_and_reraises_failure(self) -> None:
        """Roll back and re-raise an exception from the block."""
        connection = AsyncMock(spec=AsyncConnection)

        with pytest.raises(RuntimeError, match="failed"):
            async with Postgres(connection=connection).atomic():
                raise RuntimeError("failed")

        connection.rollback.assert_awaited_once()
        connection.commit.assert_not_awaited()

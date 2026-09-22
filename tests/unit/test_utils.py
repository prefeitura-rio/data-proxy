"""Unit tests for asynchronous transaction and readiness helpers."""

from typing import cast

import pytest
from psycopg import AsyncConnection

from data_proxy.utils import atomic, wait_for
from tests.fixtures.unit import TransactionConnection


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
    async def test_commits_after_success(
        self, transaction_connection: TransactionConnection
    ) -> None:
        """Commit the connection after a successful block."""
        connection = transaction_connection
        async with atomic(cast("AsyncConnection", cast(object, connection))):
            pass
        assert connection.commits == 1
        assert connection.rollbacks == 0

    @pytest.mark.asyncio
    async def test_rolls_back_and_reraises_failure(
        self, transaction_connection: TransactionConnection
    ) -> None:
        """Roll back and re-raise an exception from the block."""
        connection = transaction_connection
        with pytest.raises(RuntimeError, match="failed"):
            async with atomic(cast("AsyncConnection", cast(object, connection))):
                raise RuntimeError("failed")
        assert connection.rollbacks == 1
        assert connection.commits == 0

"""Unit tests for the PostgreSQL transaction helper."""

from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection

from data_proxy.postgres import Postgres


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

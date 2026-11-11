import asyncio
from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection

from sync.postgres import Postgres
from tests.helpers.core import exactly


class TestAtomic:
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

        with pytest.raises(RuntimeError, match=exactly("failed")):
            async with Postgres(connection=connection).atomic():
                raise RuntimeError("failed")

        connection.rollback.assert_awaited_once()
        connection.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rolls_back_a_cancellation_too(self) -> None:
        """A cancelled task is not an Exception, and it must not leave the transaction open."""
        connection = AsyncMock(spec=AsyncConnection)

        with pytest.raises(asyncio.CancelledError):
            async with Postgres(connection=connection).atomic():
                raise asyncio.CancelledError

        connection.rollback.assert_awaited_once()
        connection.commit.assert_not_awaited()

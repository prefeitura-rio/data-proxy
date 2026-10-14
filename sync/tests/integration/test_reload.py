from asyncio import timeout
from time import perf_counter

import pytest
from psycopg import AsyncConnection

from sync.postgres import Postgres
from sync.schema import notify_postgrest, wait_for_standbys
from sync.settings import settings


@pytest.mark.postgres
class TestReloadPostgrest:
    """PostgREST reads the schema again, after the standbys replayed the change."""

    async def test_tells_postgrest_to_reload(self) -> None:
        """A listener on the pgrst channel gets the reload message."""
        async with (
            await AsyncConnection.connect(
                settings.PG_DATABASE_URL, autocommit=True
            ) as listener,
            Postgres.connect(settings.PG_DATABASE_URL) as pg_conn,
        ):
            await listener.execute(b"LISTEN pgrst")

            await notify_postgrest(pg_conn)

            async with timeout(2):
                async for notification in listener.notifies():
                    assert notification.payload == "reload schema"
                    break

    async def test_does_not_wait_when_no_standby_streams(self) -> None:
        """A cluster with one instance needs no wait."""
        async with Postgres.connect(settings.PG_DATABASE_URL) as pg_conn:
            started = perf_counter()

            await wait_for_standbys(pg_conn)

            assert perf_counter() - started < 1

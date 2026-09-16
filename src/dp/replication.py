"""PostgreSQL replication readiness helpers."""

from psycopg import AsyncConnection

from .executor import execute_sql
from .settings import settings
from .utils import atomic, wait_for


async def current_wal_lsn(pg_conn: AsyncConnection) -> str:
    """Return the primary WAL position after committed work."""
    async with atomic(pg_conn):
        cursor = await execute_sql(pg_conn, "postgres/current_wal_lsn")
        row = await cursor.fetchone()

    match row:
        case (str() as lsn,):
            return lsn
        case _:
            raise RuntimeError("PostgreSQL did not return a WAL position")


async def wait_for_replica_replay(pg_conn: AsyncConnection, target_lsn: str) -> None:
    """Wait until every streaming standby has replayed the target WAL position."""

    async def replicas_replayed() -> None:
        async with atomic(pg_conn):
            cursor = await execute_sql(
                pg_conn,
                "postgres/replicas_replayed",
                params=(target_lsn,),
            )

            row = await cursor.fetchone()

            if not row or row[0] is not True:
                raise RuntimeError("PostgreSQL standbys are still behind")

    await wait_for(
        replicas_replayed,
        timeout=settings.REPLICATION_WAIT_TIMEOUT_SECONDS,
        interval=settings.REPLICATION_POLL_INTERVAL_SECONDS,
        message="PostgreSQL standbys did not replay the publication WAL",
    )

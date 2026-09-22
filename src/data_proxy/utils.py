"""Shared async helpers for the synchronization pipeline."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from tenacity import retry, stop_after_delay, wait_fixed


async def wait_for(
    check: Callable[[], Awaitable[None]],
    *,
    timeout: float,
    interval: float,
    message: str,
) -> None:
    """Retry an async readiness check until it succeeds or times out."""

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_fixed(interval),
        reraise=True,
    )
    async def attempt() -> None:
        await check()

    try:
        await attempt()
    except Exception as error:
        raise TimeoutError(message) from error


@asynccontextmanager
async def atomic(pg_conn: AsyncConnection) -> AsyncGenerator[None]:
    """Commit on success and roll back on exception without a savepoint."""
    try:
        yield
    except Exception:
        await pg_conn.rollback()
        raise
    await pg_conn.commit()

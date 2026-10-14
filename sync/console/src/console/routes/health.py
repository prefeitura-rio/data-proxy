"""Liveness and readiness probes."""

from logging import getLogger

from fastapi import APIRouter, HTTPException

from console.dependencies import Client, Pool
from console.models import Status

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> Status:
    """Report that the process is alive."""
    return Status(status="ok")


@router.get("/ready")
async def ready(client: Client, pool: Pool) -> Status:
    """Report that the DBOS system database and the app database answer."""
    try:
        await client.check_connection_async()
        async with pool.connection(timeout=2) as connection:
            await connection.execute("SELECT 1")
    except Exception as error:
        getLogger(__name__).exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from error

    return Status(status="ok")

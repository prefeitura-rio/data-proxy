"""Liveness and readiness probes."""

from logging import getLogger
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from console.dependencies import Client, Pool

logger = getLogger(__name__)

READY_TIMEOUT_SECONDS = 2

router = APIRouter(tags=["health"])


class Status(BaseModel):
    """The answer of a probe."""

    status: Literal["ok"]


@router.get("/health")
async def health() -> Status:
    """Report that the process is alive."""
    return Status(status="ok")


@router.get("/ready")
async def ready(client: Client, pool: Pool) -> Status:
    """Report that the DBOS system database and the app database answer."""
    try:
        await client.check_connection_async()
        async with pool.connection(timeout=READY_TIMEOUT_SECONDS) as connection:
            await connection.execute("SELECT 1")
    except Exception as error:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from error

    return Status(status="ok")

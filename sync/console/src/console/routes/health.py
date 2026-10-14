"""Liveness and readiness probes."""

import asyncio
from logging import getLogger

from fastapi import APIRouter
from sqlalchemy import text

from console.dependencies import Client, Engine
from console.models import Status
from console.problems import Problem

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> Status:
    """Report that the process is alive."""
    return Status(status="ok")


@router.get("/ready")
async def ready(client: Client, engine: Engine) -> Status:
    """Report that the DBOS system database and the app database answer."""
    try:
        await client.check_connection_async()
        async with asyncio.timeout(2), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        getLogger(__name__).exception("Readiness check failed")
        raise Problem(503, "database unavailable") from error

    return Status(status="ok")

"""Liveness and readiness probes."""

from logging import getLogger
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from console.dependencies import Client

logger = getLogger(__name__)

router = APIRouter(tags=["health"])


class Status(BaseModel):
    """The answer of a probe."""

    status: Literal["ok"]


@router.get("/health")
async def health() -> Status:
    """Report that the process is alive."""
    return Status(status="ok")


@router.get("/ready")
async def ready(client: Client) -> Status:
    """Report that the DBOS system database answers."""
    try:
        await client.check_connection_async()
    except Exception as error:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="database unavailable") from error

    return Status(status="ok")

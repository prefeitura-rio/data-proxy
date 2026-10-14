"""Liveness and readiness probes."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.dependencies import Client

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
        raise HTTPException(status_code=503, detail=str(error)) from error
    return Status(status="ok")

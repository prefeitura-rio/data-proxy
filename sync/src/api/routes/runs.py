"""Routes that show the runs of the sync workflow."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from api.dependencies import Client
from api.models import RunPage, RunStatus
from api.runs import list_runs

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
async def get_runs(
    client: Client,
    status: Annotated[RunStatus | None, Query()] = None,
    since: Annotated[datetime | None, Query(description="Earliest start time.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunPage:
    """List the sync runs, newest first."""
    return await list_runs(
        client, status=status, since=since, limit=limit, offset=offset
    )

"""Routes that show the runs of the sync workflow."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api.dependencies import Client
from api.models import RunDetail, RunPage, RunStatus
from api.runs import get_run, list_runs

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


@router.get("/{run_id}")
async def get_run_detail(run_id: str, client: Client) -> RunDetail:
    """Show one sync run with its steps and the workflows that it started."""
    run = await get_run(client, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run

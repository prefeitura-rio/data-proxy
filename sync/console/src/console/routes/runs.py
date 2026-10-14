"""Routes that show the runs of the sync workflow."""

from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

from console import streaming
from console.dependencies import Client, require_workflow
from console.models import End, RunDetail, RunPage, RunProgress, RunStatus, Time
from console.runs import get_run, list_runs
from console.settings import settings

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
async def get_runs(
    client: Client,
    status: Annotated[RunStatus | None, Query()] = None,
    since: Annotated[Time | None, Query(description="Earliest start time.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunPage:
    """List the sync runs, newest first."""
    return await list_runs(
        client, status=status, since=since, limit=limit, offset=offset
    )


@router.get("/{run_id}")
async def get_run_detail(
    run_id: str,
    client: Client,
    *,
    include_internal: Annotated[
        bool, Query(description="Also list the steps that DBOS records itself.")
    ] = False,
) -> RunDetail:
    """Show one sync run with its steps and the workflows that it started."""
    run = await get_run(client, run_id, include_internal=include_internal)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get(
    "/{workflow_id}/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(require_workflow)],
)
async def stream_run(
    workflow_id: str, client: Client
) -> AsyncIterable[ServerSentEvent]:
    """Send the phases of a run each time they change, and end when the run ends."""

    async def read() -> RunProgress | None:
        run = await get_run(client, workflow_id, include_internal=False)
        if run is None:
            return None
        return RunProgress(
            status=run.status, steps=run.steps, child_counts=run.child_counts
        )

    finished = {"SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"}
    async for progress in streaming.changes(
        read,
        interval=settings.STREAM_INTERVAL_SECONDS,
        is_last=lambda progress: progress is None or progress.status in finished,
    ):
        if progress is None:
            return
        yield ServerSentEvent(data=progress, event="run")
        if progress.status in finished:
            yield ServerSentEvent(
                data=End(workflow_id=workflow_id, status=progress.status), event="end"
            )

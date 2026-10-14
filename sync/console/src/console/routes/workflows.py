"""Routes that follow one workflow."""

from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

from console.dependencies import Client
from console.logs import follow_logs

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def require_workflow(workflow_id: str, client: Client) -> None:
    """Answer 404 before any streaming starts when the workflow is unknown."""
    found = await client.list_workflows_async(
        workflow_ids=[workflow_id], load_input=False, load_output=False
    )
    if not found:
        raise HTTPException(status_code=404, detail="workflow not found")


@router.get(
    "/{workflow_id}/logs",
    response_class=EventSourceResponse,
    dependencies=[Depends(require_workflow)],
)
async def get_logs(
    workflow_id: str,
    client: Client,
    offset: Annotated[int, Query(ge=0, description="First line to send.")] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> AsyncIterable[ServerSentEvent]:
    """Follow the log of a workflow as server-sent events, live while it runs."""
    start = offset if last_event_id is None else last_event_id + 1
    async for event in follow_logs(client, workflow_id, start):
        yield event

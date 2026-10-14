"""Routes that follow one workflow."""

from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

from console.dependencies import Client, require_workflow
from console.models import LogEnd, LogLine

router = APIRouter(prefix="/workflows", tags=["workflows"])


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
    """Follow the log of a workflow as server-sent events, live while it runs.

    The event id is the offset of the line in the stream, so a browser that
    reconnects with ``Last-Event-ID`` resumes at the next line.
    """
    position = offset if last_event_id is None else last_event_id + 1
    stream: AsyncIterable[object] = client.read_stream_async(
        workflow_id, "logs", offset=position
    )
    async for value in stream:
        yield ServerSentEvent(
            data=LogLine.model_validate(value), event="log", id=str(position)
        )
        position += 1

    workflows = await client.list_workflows_async(
        workflow_ids=[workflow_id], load_input=False, load_output=False
    )
    yield ServerSentEvent(
        data=LogEnd.model_validate(
            {"workflow_id": workflow_id, "status": workflows[0].status}
        ),
        event="end",
    )

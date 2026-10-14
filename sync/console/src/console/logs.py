"""Follow the log that a workflow writes to its DBOS stream."""

from typing import TYPE_CHECKING

from fastapi.sse import ServerSentEvent

from console.models import LogEnd, LogLine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from dbos import DBOSClient

LOG_STREAM = "logs"


async def follow_logs(
    client: DBOSClient, workflow_id: str, offset: int
) -> AsyncGenerator[ServerSentEvent]:
    """Send each log line as an event, then an end event with the workflow status.

    The event id is the offset of the line in the stream, so a browser that
    reconnects with ``Last-Event-ID`` resumes at the next line.
    """
    stream: AsyncGenerator[object] = client.read_stream_async(
        workflow_id, LOG_STREAM, offset=offset
    )
    position = offset
    async for value in stream:
        yield ServerSentEvent(
            data=LogLine.model_validate(value), event="log", id=str(position)
        )
        position += 1

    workflows = await client.list_workflows_async(
        workflow_ids=[workflow_id], load_input=False, load_output=False
    )
    end = LogEnd.model_validate(
        {"workflow_id": workflow_id, "status": workflows[0].status}
    )
    yield ServerSentEvent(data=end, event="end")

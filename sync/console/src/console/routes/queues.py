"""Routes that show the queues of the sync application."""

from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from console import streaming
from console.dependencies import Client
from console.models import Queue
from console.queues import list_queues
from console.settings import settings

router = APIRouter(prefix="/queues", tags=["queues"])


@router.get("")
async def get_queues(client: Client) -> list[Queue]:
    """List the queues with their limits and the count of their workflows."""
    return await list_queues(client)


@router.get("/stream", response_class=EventSourceResponse)
async def stream_queues(client: Client) -> AsyncIterable[ServerSentEvent]:
    """Send the queues now, and again each time a limit or a depth changes."""
    async for queues in streaming.changes(
        lambda: list_queues(client), interval=settings.STREAM_INTERVAL_SECONDS
    ):
        yield ServerSentEvent(data=queues, event="queues")

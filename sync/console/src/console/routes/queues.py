"""Routes that show the queues of the sync application."""

import asyncio
from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from console import streaming
from console.dependencies import Client
from console.models import ErrorItem, Queue, QueuePatch
from console.problems import Problem
from console.queues import list_queues, merge_limits, read_stored
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


@router.patch("/{name}")
async def patch_queue(name: str, patch: QueuePatch, client: Client) -> Queue:
    """Change the concurrency or the rate limit of a queue with a JSON Merge Patch.

    A field that the body leaves out stays. Null removes a limit. DBOS refuses limits that
    contradict each other, and the answer is 422.
    """
    queue = await client.retrieve_queue_async(name)
    if queue is None:
        raise Problem(404, "queue not found")

    stored = await asyncio.to_thread(read_stored, queue)
    merged = merge_limits(stored, patch)
    try:
        await client.register_queue_async(
            name,
            global_concurrency=merged.global_concurrency,
            worker_concurrency=merged.worker_concurrency,
            limiter=merged.limiter,
            partition_concurrency=merged.partition_concurrency,
            partition_worker_concurrency=merged.partition_worker_concurrency,
            partition_limiter=merged.partition_limiter,
            polling_interval_sec=merged.polling_interval_sec,
            on_conflict="always_update",
        )
    except ValueError as error:
        raise Problem(
            422, "DBOS refused the limits", [ErrorItem(pointer="", message=str(error))]
        ) from error

    return next(item for item in await list_queues(client) if item.name == name)

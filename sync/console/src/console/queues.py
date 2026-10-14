"""Read the queues of the sync application from DBOS."""

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

from console.models import Queue, RateLimit

if TYPE_CHECKING:
    from dbos import DBOSClient
    from dbos import Queue as DBOSQueue
    from dbos._queue import QueueRateLimit


def to_rate_limit(limiter: QueueRateLimit | None) -> RateLimit | None:
    """Describe the rate limit of a queue, if it has one."""
    if limiter is None:
        return None
    return RateLimit(limit=limiter["limit"], period_seconds=limiter["period"])


def read_queue(queue: DBOSQueue, depth: dict[str, int]) -> Queue:
    """Read the configuration of one queue.

    Each property reads the database, so the caller runs this in a thread.
    """
    return Queue(
        name=queue.name,
        global_concurrency=queue.global_concurrency,
        worker_concurrency=queue.worker_concurrency,
        rate_limit=to_rate_limit(queue.limiter),
        partitioned=any(
            limit is not None
            for limit in (
                queue.partition_concurrency,
                queue.partition_worker_concurrency,
                queue.partition_limiter,
            )
        ),
        polling_interval_seconds=queue.polling_interval_sec,
        depth=depth,
    )


async def list_queues(client: DBOSClient) -> list[Queue]:
    """List the queues by name, each with the count of its workflows by status."""
    queues = await client.list_queues_async()
    queued = await client.list_queued_workflows_async(
        load_input=False, load_output=False
    )

    depth: dict[str, Counter[str]] = {}
    for workflow in queued:
        if workflow.queue_name is not None:
            depth.setdefault(workflow.queue_name, Counter())[workflow.status] += 1

    return await asyncio.to_thread(
        lambda: sorted(
            (read_queue(queue, dict(depth.get(queue.name, {}))) for queue in queues),
            key=lambda queue: queue.name,
        )
    )

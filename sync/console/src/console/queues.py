"""Read the queues of the sync application from DBOS."""

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from console.models import Queue, QueuePatch, RateLimit

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


@dataclass(frozen=True)
class StoredQueue:
    """Every setting that DBOS stores for a queue, because a write replaces them all."""

    global_concurrency: int | None
    worker_concurrency: int | None
    limiter: QueueRateLimit | None
    partition_concurrency: int | None
    partition_worker_concurrency: int | None
    partition_limiter: QueueRateLimit | None
    polling_interval_sec: float


def read_stored(queue: DBOSQueue) -> StoredQueue:
    """Read the stored settings of one queue. Each property reads the database."""
    return StoredQueue(
        global_concurrency=queue.global_concurrency,
        worker_concurrency=queue.worker_concurrency,
        limiter=queue.limiter,
        partition_concurrency=queue.partition_concurrency,
        partition_worker_concurrency=queue.partition_worker_concurrency,
        partition_limiter=queue.partition_limiter,
        polling_interval_sec=queue.polling_interval_sec,
    )


def merge_limits(stored: StoredQueue, patch: QueuePatch) -> StoredQueue:
    """Apply a merge patch: a given field replaces the stored one, and null removes it."""
    given = patch.model_fields_set
    rate_limit = patch.rate_limit
    return StoredQueue(
        global_concurrency=patch.global_concurrency
        if "global_concurrency" in given
        else stored.global_concurrency,
        worker_concurrency=patch.worker_concurrency
        if "worker_concurrency" in given
        else stored.worker_concurrency,
        limiter=stored.limiter
        if "rate_limit" not in given
        else None
        if rate_limit is None
        else {"limit": rate_limit.limit, "period": rate_limit.period_seconds},
        partition_concurrency=stored.partition_concurrency,
        partition_worker_concurrency=stored.partition_worker_concurrency,
        partition_limiter=stored.partition_limiter,
        polling_interval_sec=stored.polling_interval_sec,
    )

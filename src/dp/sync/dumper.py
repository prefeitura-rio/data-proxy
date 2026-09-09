"""FastStream dumper application for BigQuery extraction."""

from time import monotonic
from uuid import uuid4

import uvloop
from asyncer import asyncify
from faststream import FastStream, Logger
from faststream.redis import RedisBroker, StreamSub

from ..constants import DUMP_STREAM, DUMPERS_GROUP, SEED_STREAM
from ..errors import retry_or_stop
from ..extraction import extract_task
from ..log import elapsed_ms, logger, runid, tablename
from ..metrics import metrics, tracker
from ..models import DumpSuccess, DumpTask, SeedTask
from ..settings import settings
from ..state import cleanup_consumer, complete_dump

broker = RedisBroker(
    str(settings.REDIS_URL),
    logger=logger,
)

dumper = FastStream(broker, logger=logger)

subs = {
    "new": StreamSub(
        DUMP_STREAM,
        group=DUMPERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        polling_interval=30,
    ),
    "stale": StreamSub(
        DUMP_STREAM,
        group=DUMPERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        polling_interval=30,
        min_idle_time=settings.DUMPER_VISIBILITY_TIMEOUT_MS,
    ),
}


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
@tracker("dumper")
async def dump_task(task: DumpTask, logger: Logger) -> None:
    """Dump one task, record its result, and continue to the next."""
    runid.set(task.run_id)
    tablename.set(task.table)
    started = monotonic()
    logger.info("Dump started task_id=%s", task.task_id)

    try:
        await asyncify(extract_task)(task)
    except Exception as error:
        await retry_or_stop(
            error, task, broker.publish, max_retries=settings.DUMPER_MAX_RETRIES
        )
    else:
        result = DumpSuccess()

    async with settings.redis as redis:
        remaining = await complete_dump(redis, task, result)

    if remaining == 0:
        await broker.publish(SeedTask(run_id=task.run_id), stream=SEED_STREAM)

    duration = monotonic() - started

    metrics.dump_task_duration_seconds.labels(table=task.table).observe(duration)
    metrics.dump_tasks_total.labels(table=task.table, status=result.status.value).inc()

    logger.info(
        "Dump completed task_id=%s status=%s elapsed_ms=%d remaining=%d",
        task.task_id,
        result.status.value,
        elapsed_ms(started),
        remaining,
    )


@dumper.on_shutdown
async def cleanup_consumers() -> None:
    """Remove idle dumper consumers."""
    async with settings.redis as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(redis, DUMP_STREAM, DUMPERS_GROUP, sub.consumer)


if __name__ == "__main__":
    uvloop.run(dumper.run())

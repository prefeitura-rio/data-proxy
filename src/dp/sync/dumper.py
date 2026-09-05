"""FastStream dumper application for BigQuery extraction."""

from time import monotonic
from uuid import uuid4

import uvloop
from asyncer import asyncify
from faststream import FastStream, Logger
from faststream.redis import RedisBroker, StreamSub

from ..constants import DUMP_STREAM, DUMPERS_GROUP, SEED_STREAM
from ..duckdb import connect
from ..errors import retry_or_stop
from ..extraction import extract_task
from ..log import elapsed_ms
from ..metrics import dump_task_duration_seconds, dump_tasks_total, push_to_gateway
from ..models import DumpSuccess, DumpTask, SeedTask
from ..settings import settings
from ..state import cleanup_consumer, complete_dump

broker = RedisBroker(
    str(settings.REDIS_URL),
)

dumper = FastStream(broker)

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


def extract_task_wrapper(task: DumpTask) -> None:
    """Run blocking extraction for one dump task."""
    with connect() as db:
        extract_task(task, db)


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
async def dump_task(task: DumpTask, logger: Logger) -> None:
    """Dump one task, record its result, and exit."""
    started = monotonic()

    try:
        await asyncify(extract_task_wrapper)(task)
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
    dump_task_duration_seconds.labels(table=task.table).observe(duration)
    dump_tasks_total.labels(status=result.status.value).inc()

    await push_to_gateway(settings.PUSHGATEWAY_URL, "dumper")

    logger.info(
        "Dump completed status=%s elapsed_ms=%d",
        result.status.value,
        elapsed_ms(started),
    )

    dumper.exit()


@dumper.on_shutdown
async def cleanup_consumers() -> None:
    """Remove idle dumper consumers."""
    async with settings.redis as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(redis, DUMP_STREAM, DUMPERS_GROUP, sub.consumer)


if __name__ == "__main__":
    uvloop.run(dumper.run())

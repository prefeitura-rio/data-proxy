"""FastStream dumper application for BigQuery extraction."""

from time import monotonic
from uuid import uuid4

import uvloop
from asyncer import asyncify
from faststream import FastStream
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub
from loguru import logger

from ..constants import DUMP_STREAM, DUMPERS_GROUP, SEED_STREAM
from ..duckdb import connect
from ..errors import stop_on_error
from ..extraction import extract_task
from ..log import configure_logging, elapsed_ms
from ..models import DumpFailure, DumpSuccess, DumpTask, SeedTask
from ..settings import settings
from ..state import cleanup_consumer, complete_dump

broker = RedisBroker(
    str(settings.REDIS_URL),
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)
dumper = FastStream(broker)
subs = {
    "new": StreamSub(
        DUMP_STREAM, group=DUMPERS_GROUP, consumer=str(uuid4()), max_records=1
    ),
    "stale": StreamSub(
        DUMP_STREAM,
        group=DUMPERS_GROUP,
        consumer=str(uuid4()),
        max_records=1,
        min_idle_time=settings.DUMPER_VISIBILITY_TIMEOUT_MS,
    ),
}


def extract_task_wrapper(task: DumpTask) -> None:
    """Run blocking extraction for one dump task."""
    with connect() as db:
        extract_task(task, db)


@broker.subscriber(stream=subs["new"])
@broker.subscriber(stream=subs["stale"])
async def dump_task(task: DumpTask) -> None:
    """Dump one task, record its result, and exit."""
    log = logger.bind(component="dumper", run_id=task.run_id, task_id=task.task_id)
    started = monotonic()
    try:
        await asyncify(extract_task_wrapper)(task)
    except Exception as error:
        log.opt(exception=error).error("Dump failed")
        result = DumpFailure(failed_path=task.bucket_path)
    else:
        result = DumpSuccess()

    async with settings.redis as redis:
        remaining = await complete_dump(redis, task, result)

    if remaining == 0:
        await broker.publish(SeedTask(run_id=task.run_id), stream=SEED_STREAM)
    log.info(
        "Dump completed", status=result.status.value, elapsed_ms=elapsed_ms(started)
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
    configure_logging()
    uvloop.run(dumper.run())

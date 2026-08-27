"""FastStream worker application for BigQuery extraction tasks."""

from uuid import uuid4

import uvloop
from asyncer import asyncify
from faststream import FastStream
from faststream.middlewares import ExceptionMiddleware
from faststream.redis import RedisBroker, StreamSub
from loguru import logger

from ..constants import (
    SYNC_FINALIZE_STREAM,
    SYNC_SHUTDOWN_CHANNEL,
    SYNC_TASKS_STREAM,
    WORKERS_GROUP,
)
from ..duckdb import connect
from ..errors import stop_on_error
from ..extraction import extract_task
from ..logging import configure_logging
from ..models import (
    FinalizeMessage,
    ShutdownMessage,
    SyncTask,
    TaskFailure,
    TaskSuccess,
)
from ..settings import settings
from ..state import cleanup_consumer, complete_task

broker = RedisBroker(
    str(settings.REDIS_URL),
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)

worker = FastStream(broker)

subs = {
    "new": StreamSub(
        SYNC_TASKS_STREAM,
        group=WORKERS_GROUP,
        consumer=str(uuid4()),
        max_records=settings.WORKER_MAX_RECORDS,
    ),
    "stale": StreamSub(
        SYNC_TASKS_STREAM,
        group=WORKERS_GROUP,
        consumer=str(uuid4()),
        max_records=settings.WORKER_MAX_RECORDS,
        min_idle_time=settings.WORKER_VISIBILITY_TIMEOUT_MS,
    ),
}


def extract_task_wrapper(task: SyncTask) -> None:
    """Run the blocking DuckDB extraction for one task."""
    with connect() as db:
        extract_task(task, db)


@broker.subscriber(SYNC_SHUTDOWN_CHANNEL)
async def handle_shutdown(message: ShutdownMessage) -> None:
    """Exit a finite worker when finalization starts."""
    logger.info("Shutdown signal for sync_id={} — exiting", message.sync_id)
    worker.exit()


@broker.subscriber(stream=subs["new"])
async def process_new_task(task: SyncTask) -> None:
    """Process a newly delivered task."""
    await process_task(task)


@broker.subscriber(stream=subs["stale"])
async def process_stale_task(task: SyncTask) -> None:
    """Process a task reclaimed from an unavailable worker."""
    await process_task(task)


async def process_task(task: SyncTask) -> None:
    """Extract one task and record its success or failure."""
    extraction_error: Exception | None = None
    try:
        await asyncify(extract_task_wrapper)(task)
    except Exception as error:
        extraction_error = error
        logger.opt(exception=error).error(
            "Extraction failed table={} sync_id={} bucket_path={}",
            task.table,
            task.sync_id,
            task.bucket_path,
        )

    outcome = (
        TaskSuccess()
        if extraction_error is None
        else TaskFailure(failed_path=task.bucket_path)
    )

    async with settings.make_redis() as redis:
        result = await complete_task(redis, task, outcome)

    if result.should_finalize:
        await broker.publish(
            FinalizeMessage(sync_id=task.sync_id),
            stream=SYNC_FINALIZE_STREAM,
        )


@worker.on_shutdown
async def cleanup_worker_consumer() -> None:
    """Remove this worker consumer when it has no pending tasks."""
    async with settings.make_redis() as redis:
        for sub in subs.values():
            assert sub.consumer is not None
            await cleanup_consumer(
                redis, SYNC_TASKS_STREAM, WORKERS_GROUP, sub.consumer
            )


if __name__ == "__main__":
    configure_logging()
    uvloop.run(worker.run())

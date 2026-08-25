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
from ..state import complete_task

broker = RedisBroker(
    str(settings.REDIS_URL),
    middlewares=(ExceptionMiddleware({Exception: stop_on_error}),),
)

worker = FastStream(broker)

CONSUMER = str(uuid4())


def extract_task_wrapper(task: SyncTask) -> None:
    """Run the blocking DuckDB extraction for one task."""
    with connect() as db:
        extract_task(task, db)


@broker.subscriber(SYNC_SHUTDOWN_CHANNEL)
async def handle_shutdown(message: ShutdownMessage) -> None:
    """Exit a finite worker when finalization starts."""
    logger.info("Shutdown signal for sync_id={} — exiting", message.sync_id)
    worker.exit()


@broker.subscriber(
    stream=StreamSub(
        SYNC_TASKS_STREAM,
        group=WORKERS_GROUP,
        consumer=CONSUMER,
        max_records=settings.WORKER_MAX_RECORDS,
    )
)
async def process_shard(task: SyncTask) -> None:
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


if __name__ == "__main__":
    configure_logging()
    uvloop.run(worker.run())

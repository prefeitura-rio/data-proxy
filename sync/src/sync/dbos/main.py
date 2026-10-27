"""Start the DBOS sync application and run it until stopped."""

from asyncio import Event
from contextlib import suppress

import uvloop
from dbos import DBOS, DBOSConfig, SetEnqueueOptions
from dbos import error as dbos_error

from sync.constants import (
    CONTROL_QUEUE,
    DUMP_QUEUE,
    PROVISION_QUEUE,
    PROXY_QUEUE,
    PUBLISH_QUEUE,
    SCHEDULES_QUEUE,
    SYNC_QUEUE,
)
from sync.log import logger
from sync.settings import settings

from .utils import endpoint_list, listened_queues, otlp_enabled
from .workflows import reconcile_schedules


async def enqueue_reconcile() -> None:
    """Queue a reconcile of the schedules, unless one waits.

    The deduplication id lets DBOS refuse a second one while the first waits in the queue, also when two workers start together.
    """
    with (
        SetEnqueueOptions(deduplication_id=reconcile_schedules.__name__),
        suppress(dbos_error.DBOSQueueDeduplicatedError),
    ):
        await DBOS.enqueue_workflow_async(SCHEDULES_QUEUE, reconcile_schedules)


async def serve() -> None:
    """Configure and launch DBOS on the running event loop, then wait until stopped."""
    config: DBOSConfig = {
        "name": settings.DBOS_APPLICATION_NAME,
        "application_version": settings.DBOS_APPLICATION_VERSION,
        "system_database_url": settings.DBOS_SYSTEM_DATABASE_URL.get_secret_value(),
        "dbos_system_schema": settings.DBOS_SYSTEM_SCHEMA,
        "enable_otlp": otlp_enabled(settings),
        "otlp_logs_endpoints": endpoint_list(settings.OTLP_LOGS_ENDPOINT),
        "otlp_traces_endpoints": endpoint_list(settings.OTLP_TRACES_ENDPOINT),
    }

    DBOS(config=config)

    queues = [
        SYNC_QUEUE,
        DUMP_QUEUE,
        PUBLISH_QUEUE,
        PROXY_QUEUE,
        PROVISION_QUEUE,
        CONTROL_QUEUE,
        SCHEDULES_QUEUE,
    ]
    DBOS.listen_queues(listened_queues(queues, settings.LISTEN_QUEUES))

    DBOS.launch()

    await DBOS.register_queue_async(
        SYNC_QUEUE,
        concurrency=settings.SYNC_QUEUE_CONCURRENCY,
        on_conflict="never_update",
    )
    await DBOS.register_queue_async(
        DUMP_QUEUE,
        worker_concurrency=settings.DUMP_QUEUE_WORKER_CONCURRENCY,
        limiter=settings.dump_queue_limiter,
        on_conflict="never_update",
    )
    await DBOS.register_queue_async(
        PUBLISH_QUEUE, partition_concurrency=1, on_conflict="never_update"
    )
    await DBOS.register_queue_async(PROXY_QUEUE, on_conflict="never_update")
    await DBOS.register_queue_async(
        PROVISION_QUEUE, concurrency=1, on_conflict="never_update"
    )
    await DBOS.register_queue_async(CONTROL_QUEUE, on_conflict="never_update")
    await DBOS.register_queue_async(
        SCHEDULES_QUEUE, concurrency=1, on_conflict="never_update"
    )

    await enqueue_reconcile()

    logger.info("Sync application started")
    await Event().wait()


def main() -> None:
    """Run the DBOS sync application on uvloop until it stops."""
    uvloop.run(serve())


if __name__ == "__main__":
    main()

"""Start the DBOS sync application and run it until stopped."""

from asyncio import Event

import uvloop
from dbos import DBOS, DBOSConfig, ScheduleInput

from sync.constants import (
    CONTROL_QUEUE,
    DUMP_QUEUE,
    PROVISION_QUEUE,
    PROXY_QUEUE,
    PUBLISH_QUEUE,
    SYNC_QUEUE,
)
from sync.log import logger
from sync.settings import settings

from .utils import endpoint_list, listened_queues, otlp_enabled
from .workflows import run_sync


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

    if await DBOS.get_schedule_async(settings.SYNC_SCHEDULE_NAME) is None:
        await DBOS.apply_schedules_async(
            [
                ScheduleInput(
                    schedule_name=settings.SYNC_SCHEDULE_NAME,
                    workflow_fn=run_sync,
                    schedule=settings.SYNC_SCHEDULE,
                    context=None,
                    queue_name=SYNC_QUEUE,
                )
            ]
        )

    logger.info("Sync application started")
    await Event().wait()


def main() -> None:
    """Run the DBOS sync application on uvloop until it stops."""
    uvloop.run(serve())


if __name__ == "__main__":
    main()

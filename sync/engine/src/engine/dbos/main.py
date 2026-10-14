"""Start the DBOS sync application and run it until stopped."""

from asyncio import Event

import uvloop
from dbos import DBOS, DBOSConfig, ScheduleInput

from engine.constants import DUMP_QUEUE, REFRESH_QUEUE, SYNC_QUEUE, publish_queue
from engine.log import logger
from engine.settings import settings

from .utils import endpoint_list, otlp_enabled
from .workflows import run_sync


async def serve() -> None:
    """Configure and launch DBOS on the running event loop, then wait until stopped."""
    config: DBOSConfig = {
        "name": settings.DBOS_APPLICATION_NAME,
        "application_version": settings.DBOS_APPLICATION_VERSION,
        "system_database_url": settings.DBOS_SYSTEM_DATABASE_URL,
        "dbos_system_schema": settings.DBOS_SYSTEM_SCHEMA,
        "enable_otlp": otlp_enabled(settings),
        "otlp_logs_endpoints": endpoint_list(settings.OTLP_LOGS_ENDPOINT),
        "otlp_traces_endpoints": endpoint_list(settings.OTLP_TRACES_ENDPOINT),
    }

    DBOS(config=config)

    publish_queues = [
        publish_queue(schema) for schema in sorted(settings.sync_config.schemas)
    ]

    DBOS.listen_queues([SYNC_QUEUE, DUMP_QUEUE, REFRESH_QUEUE, *publish_queues])

    DBOS.launch()

    await DBOS.register_queue_async(
        SYNC_QUEUE, concurrency=settings.SYNC_QUEUE_CONCURRENCY
    )
    await DBOS.register_queue_async(
        DUMP_QUEUE,
        worker_concurrency=settings.DUMP_QUEUE_WORKER_CONCURRENCY,
        limiter=settings.dump_queue_limiter,
    )

    await DBOS.register_queue_async(
        REFRESH_QUEUE, worker_concurrency=settings.READER_REFRESH_CONCURRENCY
    )

    for queue in publish_queues:
        await DBOS.register_queue_async(queue, concurrency=1)

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

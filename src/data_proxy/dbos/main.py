import threading

from dbos import DBOS, DBOSConfig, ScheduleInput

from ..constants import DUMP_QUEUE, PUBLISH_QUEUE, SYNC_QUEUE
from ..log import logger
from ..settings import settings
from .utils import endpoint_list, otlp_enabled
from .workflows import run_sync


def main() -> None:
    """Configure, launch, and run the DBOS sync application until stopped."""
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

    DBOS.listen_queues([SYNC_QUEUE, DUMP_QUEUE, PUBLISH_QUEUE])

    DBOS.launch()

    DBOS.register_queue(SYNC_QUEUE, concurrency=settings.SYNC_QUEUE_CONCURRENCY)

    DBOS.register_queue(
        DUMP_QUEUE,
        worker_concurrency=settings.DUMP_QUEUE_WORKER_CONCURRENCY,
        limiter={"limit": settings.DUMP_QUEUE_RATE_LIMIT, "period": 60.0},
    )

    DBOS.register_queue(
        PUBLISH_QUEUE,
        worker_concurrency=settings.PUBLISH_QUEUE_WORKER_CONCURRENCY,
    )

    DBOS.apply_schedules(
        [
            ScheduleInput(
                schedule_name=settings.SYNC_SCHEDULE_NAME,
                workflow_fn=run_sync,
                schedule=settings.SYNC_SCHEDULE,
                queue_name=SYNC_QUEUE,
            )
        ]
    )

    logger.info("DBOS sync application started")
    threading.Event().wait()


if __name__ == "__main__":
    main()

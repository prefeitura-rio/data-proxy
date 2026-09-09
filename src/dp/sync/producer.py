"""FastStream producer application for one synchronization run."""

from time import monotonic

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from whenever import Instant

from ..constants import DUMP_STREAM, SEED_STREAM
from ..duckdb import connect
from ..log import elapsed_ms, logger, runid
from ..metrics import metrics, tracker
from ..models import SeedTask
from ..planning import build_sync_work
from ..s3 import clear_bucket
from ..settings import settings
from ..state import create_run, ensure_groups, read_active_run, read_remaining

broker = RedisBroker(str(settings.REDIS_URL), logger=logger)
producer = FastStream(broker, logger=logger)


@producer.after_startup
@tracker("producer")
async def produce() -> None:
    """Plan one run, persist schema plans, and publish dump tasks."""
    runidval = Instant.now().format_iso()
    started = monotonic()

    async with settings.redis as redis:
        active_run = await read_active_run(redis)

        if active_run is not None:
            remaining = await read_remaining(redis, active_run)
            if remaining == 0:
                await broker.publish(SeedTask(run_id=active_run), stream=SEED_STREAM)
            metrics.producer_runs_total.labels(status="recovered").inc()

            producer.exit()
            return

        await ensure_groups(redis)

        with connect() as db:
            work = await build_sync_work(
                settings.sync_config, redis, runidval, settings.GCS_BUCKET, db
            )

        if not work.plans:
            logger.info("No table changes")
            metrics.producer_runs_total.labels(status="no_changes").inc()

            producer.exit()
            return

        if not await create_run(redis, runidval, work.plans, len(work.tasks)):
            logger.warning("An active run already exists")
            metrics.producer_runs_total.labels(status="active_run_conflict").inc()

            producer.exit()
            return

    runid.set(runidval)

    await clear_bucket()
    logger.info("Bucket cleared")

    if work.tasks:
        for task in work.tasks:
            await broker.publish(task, stream=DUMP_STREAM)
    else:
        await broker.publish(SeedTask(run_id=runidval), stream=SEED_STREAM)

    logger.info(
        "Run published tasks=%d plans=%d elapsed_ms=%d",
        len(work.tasks),
        len(work.plans),
        elapsed_ms(started),
    )
    metrics.producer_runs_total.labels(status="success").inc()

    producer.exit()


if __name__ == "__main__":
    uvloop.run(producer.run())

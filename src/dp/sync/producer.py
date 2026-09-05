"""FastStream producer application for one synchronization run."""

from time import monotonic

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from whenever import Instant

from ..constants import DUMP_STREAM, SEED_STREAM
from ..duckdb import connect
from ..log import elapsed_ms, logger
from ..metrics import producer_runs_total, push_to_gateway
from ..models import SeedTask, SyncConfig
from ..planning import build_sync_work
from ..settings import settings
from ..state import create_run, ensure_groups, read_active_run, read_remaining

broker = RedisBroker(str(settings.REDIS_URL))
producer = FastStream(broker)


@producer.after_startup
async def produce() -> None:
    """Plan one run, persist schema plans, and publish dump tasks."""
    run_id = Instant.now().format_iso()
    started = monotonic()
    config = SyncConfig.model_validate_json(settings.SYNC_CONFIG_PATH.read_text())

    async with settings.redis as redis:
        active_run = await read_active_run(redis)

        if active_run is not None:
            remaining = await read_remaining(redis, active_run)
            if remaining == 0:
                await broker.publish(SeedTask(run_id=active_run), stream=SEED_STREAM)
            producer_runs_total.labels(status="recovered").inc()
            await push_to_gateway(settings.PUSHGATEWAY_URL, "producer")

            producer.exit()
            return

        await ensure_groups(redis)

        with connect() as db:
            work = await build_sync_work(config, redis, run_id, settings.GCS_BUCKET, db)

        if not work.plans:
            logger.info("No table changes")
            producer_runs_total.labels(status="no_changes").inc()
            await push_to_gateway(settings.PUSHGATEWAY_URL, "producer")

            producer.exit()
            return

        if not await create_run(redis, run_id, work.plans, len(work.tasks)):
            logger.warning("An active run already exists")
            producer_runs_total.labels(status="active_run_conflict").inc()
            await push_to_gateway(settings.PUSHGATEWAY_URL, "producer")

            producer.exit()
            return

    if work.tasks:
        for task in work.tasks:
            await broker.publish(task, stream=DUMP_STREAM)
    else:
        await broker.publish(SeedTask(run_id=run_id), stream=SEED_STREAM)

    logger.info("Run published run_id=%s elapsed_ms=%d", run_id, elapsed_ms(started))
    producer_runs_total.labels(status="success").inc()
    await push_to_gateway(settings.PUSHGATEWAY_URL, "producer")

    producer.exit()


if __name__ == "__main__":
    uvloop.run(producer.run())

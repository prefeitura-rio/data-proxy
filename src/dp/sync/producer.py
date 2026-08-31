"""FastStream producer application for one synchronization run."""

from time import monotonic

import uvloop
from faststream import FastStream
from faststream.redis import RedisBroker
from loguru import logger
from whenever import Instant

from ..constants import DUMP_STREAM, SEED_STREAM
from ..duckdb import connect
from ..log import configure_logging, elapsed_ms
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
    async with settings.make_redis() as redis:
        active_run = await read_active_run(redis)
        if active_run is not None:
            remaining = await read_remaining(redis, active_run)
            if remaining == 0:
                await broker.publish(SeedTask(run_id=active_run), stream=SEED_STREAM)
            producer.exit()
            return
        await ensure_groups(redis)
        with connect() as db:
            work = await build_sync_work(config, redis, run_id, settings.GCS_BUCKET, db)
        if not work.plans:
            logger.info("No table changes")
            producer.exit()
            return
        if not await create_run(redis, run_id, work.plans, len(work.tasks)):
            logger.warning("An active run already exists")
            producer.exit()
            return
    if work.tasks:
        for task in work.tasks:
            await broker.publish(task, stream=DUMP_STREAM)
    else:
        await broker.publish(SeedTask(run_id=run_id), stream=SEED_STREAM)
    logger.info("Run published", run_id=run_id, elapsed_ms=elapsed_ms(started))
    producer.exit()


if __name__ == "__main__":
    configure_logging()
    uvloop.run(producer.run())

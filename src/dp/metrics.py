"""Prometheus metrics and async Pushgateway client for pipeline workers."""

from httpx2 import AsyncClient
from prometheus_client import Counter, Histogram, generate_latest

from dp.log import logger

dump_tasks_total = Counter(
    "dump_tasks_total",
    "Total dump tasks processed",
    labelnames=("status",),
)

dump_task_duration_seconds = Histogram(
    "dump_task_duration_seconds",
    "Dump task duration in seconds",
    labelnames=("table",),
)

publish_tables_total = Counter(
    "publish_tables_total",
    "Total tables published",
    labelnames=("status",),
)

publish_table_duration_seconds = Histogram(
    "publish_table_duration_seconds",
    "Table publication duration in seconds",
    labelnames=("schema",),
)

seed_runs_total = Counter(
    "seed_runs_total",
    "Total seed runs processed",
    labelnames=("status",),
)

producer_runs_total = Counter(
    "producer_runs_total",
    "Total producer runs processed",
    labelnames=("status",),
)


async def push_to_gateway(url: str, job: str) -> None:
    """Push all registered metrics to a Pushgateway endpoint via httpx.

    Fire-and-forget: any connection or HTTP error is logged and swallowed.
    """
    body = generate_latest()

    try:
        async with AsyncClient(timeout=5) as client:
            await client.post(
                f"{url}/job/{job}",
                content=body,
                headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            )
    except Exception:
        logger.debug("pushgateway unavailable", exc_info=True)

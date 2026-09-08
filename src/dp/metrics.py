"""Prometheus metrics and async Pushgateway client for pipeline workers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps

from httpx2 import AsyncClient
from prometheus_client import Counter, Histogram, generate_latest

from dp.log import logger
from dp.settings import settings


@dataclass(slots=True)
class Metrics:
    """Container for all pipeline Prometheus metrics."""

    dump_tasks_total: Counter = field(init=False)
    dump_task_duration_seconds: Histogram = field(init=False)
    publish_tables_total: Counter = field(init=False)
    publish_table_duration_seconds: Histogram = field(init=False)
    seed_runs_total: Counter = field(init=False)
    producer_runs_total: Counter = field(init=False)

    def __post_init__(self) -> None:
        """Initialize all counters and histograms."""
        self.dump_tasks_total = Counter(
            "dump_tasks_total",
            "Total dump tasks processed",
            labelnames=("table", "status"),
        )
        self.dump_task_duration_seconds = Histogram(
            "dump_task_duration_seconds",
            "Dump task duration in seconds",
            labelnames=("table",),
        )
        self.publish_tables_total = Counter(
            "publish_tables_total",
            "Total tables published",
            labelnames=("schema", "status"),
        )
        self.publish_table_duration_seconds = Histogram(
            "publish_table_duration_seconds",
            "Table publication duration in seconds",
            labelnames=("table",),
        )
        self.seed_runs_total = Counter(
            "seed_runs_total",
            "Total seed runs processed",
            labelnames=("status",),
        )
        self.producer_runs_total = Counter(
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
                f"{url}/metrics/job/{job}",
                content=body,
                headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            )
    except Exception:
        logger.debug("pushgateway unavailable", exc_info=True)


def tracker[**P](
    job: str,
) -> Callable[[Callable[P, Awaitable[None]]], Callable[P, Awaitable[None]]]:
    """Decorate an async worker to push metrics to Pushgateway after it completes."""

    def decorator(fn: Callable[P, Awaitable[None]]) -> Callable[P, Awaitable[None]]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
            await fn(*args, **kwargs)
            await push_to_gateway(settings.PUSHGATEWAY_URL, job)

        return wrapper

    return decorator


metrics = Metrics()

"""OpenTelemetry metrics for sync workers."""

from dataclasses import dataclass, field
from functools import partial, wraps
from typing import TYPE_CHECKING

from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from .settings import settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from opentelemetry.metrics import Counter

    from .types import StatusRecorder


def configure_metrics() -> None:
    """Configure the OTLP metric exporter when an endpoint is set."""
    endpoint = settings.OTLP_METRICS_ENDPOINT
    if len(endpoint) == 0:
        return

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
    otel_metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))


configure_metrics()
meter = otel_metrics.get_meter("data_proxy")


def create_counter(name: str, description: str) -> Counter:
    """Create one counter of the sync meter."""
    return meter.create_counter(name, description=description, unit="1")


@dataclass(slots=True)
class Metrics:
    """Container for all sync OpenTelemetry metrics."""

    dump_tasks_total: Counter = field(
        default_factory=partial(
            create_counter, "dump_tasks_total", "Total dump tasks processed"
        )
    )
    publish_tables_total: Counter = field(
        default_factory=partial(
            create_counter, "publish_tables_total", "Total tables published"
        )
    )
    seed_runs_total: Counter = field(
        default_factory=partial(
            create_counter, "seed_runs_total", "Total seed runs processed"
        )
    )
    sync_runs_total: Counter = field(
        default_factory=partial(
            create_counter, "sync_runs_total", "Total sync runs processed"
        )
    )


metrics = Metrics()


def observe[**P](
    record_status: StatusRecorder,
) -> Callable[[Callable[P, Awaitable[None]]], Callable[P, Coroutine[None, None, None]]]:
    """Record success or failure for a void workflow."""

    def decorate(
        workflow: Callable[P, Awaitable[None]],
    ) -> Callable[P, Coroutine[None, None, None]]:
        @wraps(workflow)
        async def observed(*args: P.args, **kwargs: P.kwargs) -> None:
            try:
                await workflow(*args, **kwargs)
            except Exception:
                await record_status("failure")
                raise

            await record_status("success")

        return observed

    return decorate

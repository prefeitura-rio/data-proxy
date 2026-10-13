"""OpenTelemetry metrics for sync workers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps

from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from .settings import settings
from .types import StatusRecorder


def configure_metrics() -> None:
    """Configure the OTLP metric exporter when an endpoint is set."""
    endpoint = settings.OTLP_METRICS_ENDPOINT
    if not endpoint:
        return

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
    otel_metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))


configure_metrics()
meter = otel_metrics.get_meter("data_proxy")


@dataclass(slots=True)
class Metrics:
    """Container for all sync OpenTelemetry metrics."""

    dump_tasks_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "dump_tasks_total", description="Total dump tasks processed", unit="1"
        )
    )
    publish_tables_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "publish_tables_total", description="Total tables published", unit="1"
        )
    )
    seed_runs_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "seed_runs_total", description="Total seed runs processed", unit="1"
        )
    )
    sync_runs_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "sync_runs_total", description="Total sync runs processed", unit="1"
        )
    )


metrics = Metrics()


def observe[**P](record_status: StatusRecorder):
    """Record success or failure for a void workflow."""

    def decorate(workflow: Callable[P, Awaitable[None]]):
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

"""OpenTelemetry metrics for pipeline workers."""

from dataclasses import dataclass, field

from opentelemetry.metrics import Counter, get_meter

meter = get_meter("dp")


@dataclass(slots=True)
class Metrics:
    """Container for all pipeline OpenTelemetry metrics."""

    dump_tasks_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "dump_tasks_total",
            description="Total dump tasks processed",
            unit="1",
        )
    )
    publish_tables_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "publish_tables_total",
            description="Total tables published",
            unit="1",
        )
    )
    seed_runs_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "seed_runs_total",
            description="Total seed runs processed",
            unit="1",
        )
    )
    sync_runs_total: Counter = field(
        default_factory=lambda: meter.create_counter(
            "sync_runs_total",
            description="Total sync runs processed",
            unit="1",
        )
    )


metrics = Metrics()

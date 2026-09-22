"""OpenTelemetry metrics for pipeline workers."""

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from functools import wraps
from typing import Literal, ParamSpec

from opentelemetry.metrics import Counter, get_meter

meter = get_meter("dp")
RunStatus = Literal["success", "no_changes", "failure"]
P = ParamSpec("P")
StatusRecorder = Callable[[RunStatus], Awaitable[None]]
SyncWorkflow = Callable[P, Awaitable[RunStatus]]
ObservedWorkflow = Callable[P, Coroutine[object, object, None]]


@dataclass(slots=True)
class Metrics:
    """Container for all pipeline OpenTelemetry metrics."""

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


def observe_sync(
    record_status: StatusRecorder,
) -> Callable[
    [Callable[P, Awaitable[RunStatus]]], Callable[P, Coroutine[object, object, None]]
]:
    """Record one terminal status for a sync workflow."""

    def decorate(
        workflow: SyncWorkflow[P],
    ) -> ObservedWorkflow[P]:
        @wraps(workflow)
        async def observed(*args: P.args, **kwargs: P.kwargs) -> None:
            try:
                status = await workflow(*args, **kwargs)
            except Exception:
                await record_status("failure")
                raise

            await record_status(status)

        return observed

    return decorate

"""The response models of the console."""

from datetime import datetime
from typing import Annotated, Literal, TypedDict

from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    PlainValidator,
    WithJsonSchema,
)
from whenever import Instant


def parse_time(value: object) -> Instant:
    """Read an ISO 8601 timestamp that has an offset, or pass an instant through."""
    match value:
        case Instant():
            return value
        case datetime():
            return Instant(value)
        case str():
            return Instant.parse_iso(value)
        case _:
            raise ValueError("expected an ISO 8601 timestamp")


Time = Annotated[
    Instant,
    PlainValidator(parse_time),
    PlainSerializer(Instant.format_iso, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]

RunStatus = Literal[
    "DELAYED",
    "ENQUEUED",
    "PENDING",
    "SUCCESS",
    "ERROR",
    "CANCELLED",
    "MAX_RECOVERY_ATTEMPTS_EXCEEDED",
]


class RunFields(TypedDict):
    """The fields that a run and a run with its detail share."""

    id: str
    status: RunStatus
    trigger: Literal["schedule", "manual"]
    schedule_name: str | None
    queue: str | None
    created_at: Time | None
    started_at: Time | None
    finished_at: Time | None
    duration_seconds: float | None
    error: str | None


class Run(BaseModel):
    """One run of the sync workflow."""

    id: str
    status: RunStatus
    trigger: Literal["schedule", "manual"]
    schedule_name: str | None
    queue: str | None
    created_at: Time | None
    started_at: Time | None
    finished_at: Time | None
    duration_seconds: float | None
    error: str | None


class Step(BaseModel):
    """One step of a workflow."""

    id: int
    name: str
    started_at: Time | None
    finished_at: Time | None
    duration_seconds: float | None
    child_workflow_id: str | None
    error: str | None


class ChildRun(BaseModel):
    """A workflow that a run started, such as one dump task or one schema publish."""

    id: str
    name: str
    status: RunStatus
    started_at: Time | None
    finished_at: Time | None
    duration_seconds: float | None
    error: str | None


class RunDetail(Run):
    """A run with its steps and child workflows."""

    steps: list[Step]
    children: list[ChildRun]
    child_counts: dict[str, dict[str, int]]


class RunPage(BaseModel):
    """One page of runs, newest first."""

    items: list[Run]
    limit: int
    offset: int
    has_more: bool


class RateLimit(BaseModel):
    """At most this many workflows start in each period."""

    limit: int
    period_seconds: float


class Queue(BaseModel):
    """A DBOS queue with its limits and the workflows that it holds."""

    name: str
    global_concurrency: int | None
    worker_concurrency: int | None
    rate_limit: RateLimit | None
    partitioned: bool
    polling_interval_seconds: float
    depth: dict[str, int]


class Schedule(BaseModel):
    """A cron schedule that starts a workflow."""

    id: str
    name: str
    workflow_name: str
    cron: str
    status: Literal["ACTIVE", "PAUSED"]
    last_fired_at: Time | None
    automatic_backfill: bool
    timezone: str | None
    queue: str | None


class LogLine(BaseModel):
    """One line of the log that a workflow wrote to its DBOS stream."""

    time: Time
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    message: str
    fields: dict[str, str | int | float | bool]
    workflow_id: str
    schema_name: str | None = Field(validation_alias="schema")
    table_name: str | None = Field(validation_alias="table")


class LogEnd(BaseModel):
    """The last event of a log stream: the workflow has ended."""

    workflow_id: str
    status: RunStatus


class Status(BaseModel):
    """The answer of a probe."""

    status: Literal["ok"]


class ErrorItem(BaseModel):
    """One problem in a request, with a JSON Pointer to the part that causes it."""

    pointer: str
    message: str


class Change(BaseModel):
    """One part of the config that a write adds, removes or changes."""

    pointer: str
    change: Literal["added", "removed", "changed"]


class ConfigCheck(BaseModel):
    """The result of a dry run: is the config valid, and what would change."""

    valid: bool
    errors: list[ErrorItem]
    changes: list[Change]


class Revision(BaseModel):
    """One stored version of the config."""

    id: int
    created_at: Time
    author: str
    comment: str
    active: bool


class RevisionDetail(Revision):
    """A revision with its document."""

    document: dict[str, object]


class Config(BaseModel):
    """The active config. The revision is None before the first write."""

    revision: Revision | None
    document: dict[str, object]

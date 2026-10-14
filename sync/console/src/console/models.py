"""The response models of the console."""

from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, PlainSerializer, PlainValidator, WithJsonSchema
from whenever import Instant


def parse_time(value: object) -> Instant:
    """Read an ISO 8601 timestamp that has an offset, or pass an instant through."""
    if isinstance(value, Instant):
        return value
    if isinstance(value, str):
        return Instant.parse_iso(value)
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

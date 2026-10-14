"""The response models of the dashboard API."""

from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel

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
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    error: str | None


class Run(BaseModel):
    """One run of the sync workflow."""

    id: str
    status: RunStatus
    trigger: Literal["schedule", "manual"]
    schedule_name: str | None
    queue: str | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    error: str | None


class Step(BaseModel):
    """One step of a workflow."""

    id: int
    name: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    child_workflow_id: str | None
    error: str | None


class ChildRun(BaseModel):
    """A workflow that a run started, such as one dump task or one schema publish."""

    id: str
    name: str
    status: RunStatus
    started_at: datetime | None
    finished_at: datetime | None
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

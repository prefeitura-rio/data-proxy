"""The response models of the dashboard API."""

from datetime import datetime
from typing import Literal

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


class RunPage(BaseModel):
    """One page of runs, newest first."""

    items: list[Run]
    limit: int
    offset: int
    has_more: bool

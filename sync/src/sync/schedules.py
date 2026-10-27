"""The DBOS schedules of the schemas: one schedule for each schema."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from croniter import croniter

from .constants import LEGACY_SCHEDULE, SCHEDULE_PREFIX

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Collection


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    """The schemas that need a schedule, and the schedules that no schema needs."""

    create: list[str]
    delete: list[str]


def schedule_name(schema: str) -> str:
    """Return the name of the schedule of one schema."""
    return f"{SCHEDULE_PREFIX}{schema}"


def plan_schedules(schemas: Collection[str], existing: Collection[str]) -> SchedulePlan:
    """Plan the changes that make one schedule exist for each schema.

    A schedule of a removed schema goes, and so does the old global schedule. A schedule without the prefix stays.
    """
    kept = set(existing)
    wanted = {schedule_name(schema) for schema in schemas}

    return SchedulePlan(
        create=sorted(
            schema for schema in schemas if schedule_name(schema) not in kept
        ),
        delete=sorted(
            name
            for name in kept
            if name == LEGACY_SCHEDULE
            or (name.startswith(SCHEDULE_PREFIX) and name not in wanted)
        ),
    )


def is_cron(text: str) -> bool:
    """Tell if DBOS reads the text as a cron expression: 5 fields, or 6 with the seconds first."""
    return croniter.is_valid(text, second_at_beginning=True)

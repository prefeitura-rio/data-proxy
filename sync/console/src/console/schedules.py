"""Read the schedules of the sync application from DBOS."""

import asyncio
from typing import TYPE_CHECKING

from dbos._error import DBOSException  # ruff: ignore[import-private-name]

from console.models import ErrorItem, Schedule, SchedulePatch
from console.problems import Problem

if TYPE_CHECKING:
    from dbos import DBOSClient, WorkflowSchedule


def to_schedule(record: WorkflowSchedule) -> Schedule:
    """Describe one DBOS schedule record, without its serialized context."""
    return Schedule.model_validate(
        {
            "id": record["schedule_id"],
            "name": record["schedule_name"],
            "workflow_name": record["workflow_name"],
            "cron": record["schedule"],
            "status": record["status"],
            "last_fired_at": record["last_fired_at"],
            "automatic_backfill": record["automatic_backfill"],
            "timezone": record["cron_timezone"],
            "queue": record["queue_name"],
        }
    )


async def find_schedule(client: DBOSClient, name: str) -> WorkflowSchedule:
    """Find one schedule by name, or answer 404."""
    records = await client.list_schedules_async()
    record = next((item for item in records if item["schedule_name"] == name), None)
    if record is None:
        raise Problem(404, "schedule not found")
    return record


def is_replacement(patch: SchedulePatch) -> bool:
    """Tell whether the patch changes what DBOS stores besides the status."""
    return patch.cron is not None or "timezone" in patch.model_fields_set


async def replace_schedule(
    client: DBOSClient, record: WorkflowSchedule, patch: SchedulePatch
) -> None:
    """Replace a schedule with a new cron or time zone, and keep everything else.

    DBOS refuses a cron or a time zone that it can't parse, and the answer is 422.
    """
    zone = (
        patch.timezone
        if "timezone" in patch.model_fields_set
        else record["cron_timezone"]
    )
    try:
        await client.apply_schedules_async(
            [
                {
                    "schedule_name": record["schedule_name"],
                    "workflow_name": record["workflow_name"],
                    "schedule": patch.cron or record["schedule"],
                    "cron_timezone": zone,
                    "queue_name": record["queue_name"],
                    "automatic_backfill": record["automatic_backfill"],
                    # ponytail: the sync schedule carries no context, so none is kept.
                    # Read and pass the stored context if another schedule is ever managed here.
                    "context": None,
                }
            ]
        )
    except DBOSException as error:
        pointer = "/cron" if "cron" in str(error).lower() else "/timezone"
        raise Problem(
            422,
            "DBOS refused the schedule",
            [ErrorItem(pointer=pointer, message=str(error))],
        ) from error


async def settle_status(
    client: DBOSClient,
    record: WorkflowSchedule,
    patch: SchedulePatch,
    *,
    replaced: bool,
) -> None:
    """Pause or resume the schedule so that it ends in the status that the patch asks for.

    A replaced schedule is active again, whatever it was before.
    """
    wanted = patch.status or record["status"]
    current = "ACTIVE" if replaced else record["status"]
    if wanted != current:
        change = client.pause_schedule if wanted == "PAUSED" else client.resume_schedule
        await asyncio.to_thread(change, record["schedule_name"])

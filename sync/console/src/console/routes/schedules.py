"""Routes that show and change the schedules of the sync application."""

from fastapi import APIRouter

from console.dependencies import Client
from console.models import Schedule, SchedulePatch
from console.schedules import (
    find_schedule,
    is_replacement,
    replace_schedule,
    settle_status,
    to_schedule,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def get_schedules(client: Client) -> list[Schedule]:
    """List the schedules by name, without their serialized context."""
    records = await client.list_schedules_async()
    return sorted(
        (to_schedule(record) for record in records), key=lambda schedule: schedule.name
    )


@router.patch("/{name}")
async def patch_schedule(name: str, patch: SchedulePatch, client: Client) -> Schedule:
    """Change the cron, the time zone or the status of a schedule with a JSON Merge Patch.

    A field that the body leaves out stays. A new cron or time zone replaces the schedule,
    and DBOS makes the replacement active, so a paused schedule is paused again afterwards.
    """
    record = await find_schedule(client, name)
    replaced = is_replacement(patch)
    if replaced:
        await replace_schedule(client, record, patch)
    await settle_status(client, record, patch, replaced=replaced)
    return to_schedule(await find_schedule(client, name))

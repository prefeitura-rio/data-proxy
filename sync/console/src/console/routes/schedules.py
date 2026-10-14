"""Routes that show the schedules of the sync application."""

from fastapi import APIRouter

from console.dependencies import Client
from console.models import Schedule

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def get_schedules(client: Client) -> list[Schedule]:
    """List the schedules by name, without their serialized context."""
    records = await client.list_schedules_async()
    return sorted(
        (
            Schedule.model_validate(
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
            for record in records
        ),
        key=lambda schedule: schedule.name,
    )

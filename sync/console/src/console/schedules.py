"""Read the schedules of the sync application from DBOS."""

from typing import TYPE_CHECKING

from console.models import Schedule

if TYPE_CHECKING:
    from dbos import DBOSClient, WorkflowSchedule


def to_schedule(record: WorkflowSchedule) -> Schedule:
    """Describe one DBOS schedule, without its serialized context."""
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


async def list_schedules(client: DBOSClient) -> list[Schedule]:
    """List the schedules by name."""
    schedules = await client.list_schedules_async()
    return sorted(
        (to_schedule(record) for record in schedules), key=lambda item: item.name
    )

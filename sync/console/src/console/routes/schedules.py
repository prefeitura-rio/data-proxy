"""Routes that show the schedules of the sync application."""

from fastapi import APIRouter

from console.dependencies import Client
from console.models import Schedule
from console.schedules import list_schedules

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def get_schedules(client: Client) -> list[Schedule]:
    """List the schedules with their cron, status and last firing."""
    return await list_schedules(client)

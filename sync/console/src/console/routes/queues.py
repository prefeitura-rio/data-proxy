"""Routes that show the queues of the sync application."""

from fastapi import APIRouter

from console.dependencies import Client
from console.models import Queue
from console.queues import list_queues

router = APIRouter(prefix="/queues", tags=["queues"])


@router.get("")
async def get_queues(client: Client) -> list[Queue]:
    """List the queues with their limits and the count of their workflows."""
    return await list_queues(client)

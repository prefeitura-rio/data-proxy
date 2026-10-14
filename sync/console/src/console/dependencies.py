"""Dependencies that the routes of the console share."""

from typing import Annotated

from dbos import DBOSClient
from fastapi import Depends, HTTPException
from psycopg_pool import AsyncConnectionPool

from console.state import AppRequest


def get_client(request: AppRequest) -> DBOSClient:
    """Return the DBOS client that the lifespan opened at startup."""
    return request.state["client"]


def get_pool(request: AppRequest) -> AsyncConnectionPool:
    """Return the connection pool that the lifespan opened at startup."""
    return request.state["pool"]


Client = Annotated[DBOSClient, Depends(get_client)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


async def require_workflow(workflow_id: str, client: Client) -> None:
    """Answer 404 when the workflow is unknown."""
    found = await client.list_workflows_async(
        workflow_ids=[workflow_id], load_input=False, load_output=False
    )
    if not found:
        raise HTTPException(status_code=404, detail="workflow not found")

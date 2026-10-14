"""Dependencies that the routes of the console share."""

from typing import Annotated

from dbos import DBOSClient
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine

from console.state import AppRequest


def get_client(request: AppRequest) -> DBOSClient:
    """Return the DBOS client that the lifespan opened at startup."""
    return request.state["client"]


def get_engine(request: AppRequest) -> AsyncEngine:
    """Return the database engine that the lifespan created at startup."""
    return request.state["engine"]


Client = Annotated[DBOSClient, Depends(get_client)]
Engine = Annotated[AsyncEngine, Depends(get_engine)]


async def require_workflow(workflow_id: str, client: Client) -> None:
    """Answer 404 when the workflow is unknown."""
    found = await client.list_workflows_async(
        workflow_ids=[workflow_id], load_input=False, load_output=False
    )
    if not found:
        raise HTTPException(status_code=404, detail="workflow not found")

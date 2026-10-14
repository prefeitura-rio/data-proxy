"""Dependencies that the routes of the console share."""

from typing import Annotated

from fastapi import Depends

from console.client import WorkflowClient
from console.state import AppRequest


def get_client(request: AppRequest) -> WorkflowClient:
    """Return the DBOS client that the lifespan opened at startup."""
    return request.state["client"]


Client = Annotated[WorkflowClient, Depends(get_client)]

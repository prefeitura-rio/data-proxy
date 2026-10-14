"""Dependencies that the routes of the dashboard API share."""

from typing import Annotated

from fastapi import Depends

from api.client import WorkflowClient
from api.state import AppRequest


def get_client(request: AppRequest) -> WorkflowClient:
    """Return the DBOS client that the lifespan opened at startup."""
    return request.state["client"]


Client = Annotated[WorkflowClient, Depends(get_client)]

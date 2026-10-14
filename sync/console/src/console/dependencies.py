"""Dependencies that the routes of the console share."""

from typing import Annotated

from dbos import DBOSClient
from fastapi import Depends

from console.state import AppRequest


def get_client(request: AppRequest) -> DBOSClient:
    """Return the DBOS client that the lifespan opened at startup."""
    return request.state["client"]


Client = Annotated[DBOSClient, Depends(get_client)]

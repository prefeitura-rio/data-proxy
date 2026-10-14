"""Dependencies that the routes of the console share."""

from dataclasses import dataclass
from typing import Annotated

from dbos import DBOSClient
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncEngine

from console.problems import Problem
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
        raise Problem(404, "workflow not found")


def if_match(
    header: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int:
    """Read the revision that the caller says it read, from the If-Match header."""
    if header is None:
        raise Problem(428, "Send the ETag that you read in the If-Match header")
    try:
        return int(header.removeprefix("W/").strip('"'))
    except ValueError as error:
        raise Problem(400, "If-Match must be the ETag of a config") from error


def author(
    forwarded_user: Annotated[str | None, Header(alias="X-Forwarded-User")] = None,
) -> str:
    """Read who calls from the header that the gateway sets."""
    return forwarded_user or "unknown"


IfMatch = Annotated[int, Depends(if_match)]
Author = Annotated[str, Depends(author)]


@dataclass
class Write:
    """What a write request says: the revision it read, who it is, and what it wants."""

    expected: int | None
    author: str
    comment: str
    dry_run: bool


def write(
    expected: IfMatch,
    who: Author,
    *,
    comment: str = "",
    dry_run: bool = False,
) -> Write:
    """Read the parts that every write to the config has in common."""
    return Write(expected=expected, author=who, comment=comment, dry_run=dry_run)


def resource_write(
    who: Author,
    *,
    comment: str = "",
    dry_run: bool = False,
) -> Write:
    """Read a write to one resource. Its guard is the ETag of the resource, not the revision."""
    return Write(expected=None, author=who, comment=comment, dry_run=dry_run)


Writing = Annotated[Write, Depends(write)]
ResourceWriting = Annotated[Write, Depends(resource_write)]

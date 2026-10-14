"""Routes that read and write the sync config."""

from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select, update

from console import config, revisions, streaming
from console.dependencies import Engine, IfMatch, Writing
from console.models import (
    ActiveRevision,
    Config,
    ConfigCheck,
    Revision,
    RevisionDetail,
)
from console.problems import Problem
from console.settings import settings
from console.tables import Revisions

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(engine: Engine, response: Response) -> Config:
    """Show the active config. The ETag is the id of its revision, or 0 before the first write."""
    async with engine.connect() as connection:
        current = await revisions.active(connection)
    response.headers["ETag"] = f'"{current.id if current else 0}"'
    return Config(
        revision=current,
        document=config.to_api(current.document) if current else {"schemas": {}},
    )


@router.get("/sync.json")
async def get_sync_json(
    engine: Engine,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """Serve the active config exactly as the engine reads sync.json.

    The ETag is the id of the active revision. A consumer that sends the ETag it
    holds gets 304 with no body while nothing changed.
    """
    async with engine.connect() as connection:
        current = await revisions.active(connection)
    etag = f'"{current.id if current else 0}"'
    if if_none_match is not None and if_none_match.removeprefix("W/") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(
        current.document if current else {"schemas": {}}, headers={"ETag": etag}
    )


@router.get("/stream", response_class=EventSourceResponse)
async def stream_config(engine: Engine) -> AsyncIterable[ServerSentEvent]:
    """Send the active revision now, and again each time another one becomes active."""

    async def read() -> ActiveRevision:
        statement = select(
            Revisions.revision_id,
            Revisions.created_at,
            Revisions.author,
            Revisions.comment,
            Revisions.active,
        ).where(Revisions.active.is_(True))
        async with engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        return ActiveRevision(
            revision=None if row is None else Revision.model_validate(dict(row))
        )

    async for active in streaming.changes(
        read, interval=settings.STREAM_INTERVAL_SECONDS
    ):
        yield ServerSentEvent(data=active, event="revision")


@router.put("")
async def put_config(
    body: dict[str, object], engine: Engine, writing: Writing, response: Response
) -> Config | ConfigCheck:
    """Replace the whole config. With dry_run, only check it and list the changes."""
    result = await config.change(engine, writing, lambda _: body)
    if isinstance(result, Config):
        response.headers["ETag"] = f'"{result.revision.id if result.revision else 0}"'
    return result


@router.patch("")
async def patch_config(
    body: dict[str, object], engine: Engine, writing: Writing, response: Response
) -> Config | ConfigCheck:
    """Change part of the config with a JSON Merge Patch. A null value removes a key."""
    result = await config.change(
        engine, writing, lambda before: config.merge_patch(before, body)
    )
    if isinstance(result, Config):
        response.headers["ETag"] = f'"{result.revision.id if result.revision else 0}"'
    return result


@router.get("/revisions")
async def get_revisions(
    engine: Engine,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Revision]:
    """List the revisions, newest first, without their documents."""
    statement = (
        select(
            Revisions.revision_id,
            Revisions.created_at,
            Revisions.author,
            Revisions.comment,
            Revisions.active,
        )
        .order_by(Revisions.revision_id.desc())
        .limit(limit)
        .offset(offset)
    )
    async with engine.connect() as connection:
        result = await connection.execute(statement)
    return [Revision.model_validate(dict(row)) for row in result.mappings()]


@router.get("/revisions/{revision_id}")
async def get_revision(revision_id: int, engine: Engine) -> RevisionDetail:
    """Show one revision with its document."""
    async with engine.connect() as connection:
        found = await revisions.get(connection, revision_id)
    if found is None:
        raise Problem(404, "Revision not found")
    return found.model_copy(update={"document": config.to_api(found.document)})


@router.post("/revisions/{revision_id}/activate")
async def activate_revision(
    revision_id: int, engine: Engine, expected: IfMatch, response: Response
) -> Config:
    """Roll back to an older revision by making it the active one."""
    async with engine.begin() as connection:
        await revisions.lock_active(connection, expected)
        target = await revisions.get(connection, revision_id)
        if target is None:
            raise Problem(404, "Revision not found")
        await revisions.deactivate(connection)
        await connection.execute(
            update(Revisions.table)
            .where(Revisions.revision_id == revision_id)
            .values(active=True)
        )
    response.headers["ETag"] = f'"{revision_id}"'
    return Config(
        revision=target.model_copy(update={"active": True}),
        document=config.to_api(target.document),
    )

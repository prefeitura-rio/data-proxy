"""Routes that read and write the sync config."""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import select, update

from console import config, revisions
from console.dependencies import Engine, IfMatch, Writing
from console.models import Config, ConfigCheck, Revision, RevisionDetail
from console.problems import Problem
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

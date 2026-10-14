"""Read and write the config revisions in the database."""

from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from console.models import RevisionDetail
from console.problems import Problem
from console.tables import Revisions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

SUMMARY = (
    Revisions.revision_id,
    Revisions.created_at,
    Revisions.author,
    Revisions.comment,
    Revisions.active,
)
DETAIL = (*SUMMARY, Revisions.document)


async def deactivate(connection: AsyncConnection) -> None:
    """Make no revision active."""
    await connection.execute(
        update(Revisions.table).where(Revisions.active.is_(True)).values(active=False)
    )


async def get(connection: AsyncConnection, revision_id: int) -> RevisionDetail | None:
    """Read one revision with its document."""
    result = await connection.execute(
        select(*DETAIL).where(Revisions.revision_id == revision_id)
    )
    row = result.mappings().first()
    return None if row is None else RevisionDetail.model_validate(dict(row))


async def active(
    connection: AsyncConnection, *, lock: bool = False
) -> RevisionDetail | None:
    """Read the active revision with its document, and lock its row when asked."""
    statement = select(*DETAIL).where(Revisions.active.is_(True))
    result = await connection.execute(
        statement.with_for_update() if lock else statement
    )
    row = result.mappings().first()
    return None if row is None else RevisionDetail.model_validate(dict(row))


async def lock_active(
    connection: AsyncConnection, expected: int
) -> RevisionDetail | None:
    """Lock the active revision and refuse when it is not the one that the caller read.

    The expected id is 0 when the caller read no revision at all.
    """
    current = await active(connection, lock=True)
    if (current.id if current else 0) != expected:
        raise Problem(412, "The config changed since you read it")
    return current


async def save(
    connection: AsyncConnection,
    document: dict[str, object],
    author: str,
    comment: str,
) -> RevisionDetail:
    """Store a document as the new active revision."""
    await deactivate(connection)
    try:
        result = await connection.execute(
            Revisions.table.insert()
            .values(author=author, comment=comment, document=document, active=True)
            .returning(Revisions.revision_id)
        )
    except IntegrityError as error:
        raise Problem(412, "The config changed since you read it") from error
    saved = await get(connection, result.scalar_one())
    if saved is None:
        raise RuntimeError("The revision that was just saved is missing")
    return saved

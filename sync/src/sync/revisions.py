"""Read draft configurations and record the result of their validation."""

from json import dumps
from typing import TYPE_CHECKING

from psycopg.rows import TupleRow

from .executor import Executor
from .models import LoadedConfig
from .schema import schema
from .types import PostgresParams

if TYPE_CHECKING:
    from .postgres import Postgres
    from .validation import Problem


async def read_revision(pg_conn: Postgres, revision_id: int) -> str | None:
    """Read the document of one revision, or nothing when it doesn't exist."""
    found = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/read_revision",
        mapping={"schema": schema()},
        params={"revision_id": revision_id},
        expect=tuple[str],
    )

    return found[0][0] if found else None


async def read_active_config(pg_conn: Postgres) -> LoadedConfig | None:
    """Read the active revision, or nothing while no revision is active."""
    found = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/read_active_revision",
        mapping={"schema": schema()},
        expect=tuple[str, str],
    )

    return LoadedConfig(revision=found[0][0], document=found[0][1]) if found else None


async def write_validation(
    pg_conn: Postgres, revision_id: int, document: str, problems: list[Problem]
) -> bool:
    """Record a validation result and report whether it still applied.

    The result applies only while the revision holds the document that was checked.
    This does not commit; the caller is responsible for committing the transaction.
    """
    recorded = await Executor[PostgresParams, list[TupleRow]](conn=pg_conn).query(
        "postgres/write_validation",
        mapping={"schema": schema()},
        params={
            "revision_id": revision_id,
            "document": document,
            "valid": not problems,
            "errors": dumps([problem.model_dump() for problem in problems])
            if problems
            else None,
        },
        expect=tuple[int],
    )

    return bool(recorded)

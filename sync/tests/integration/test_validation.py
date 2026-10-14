import asyncio

import psycopg
import pytest
from psycopg.types.json import Jsonb

from sync.postgres import Postgres
from sync.revisions import read_revision, write_validation
from sync.validation import Problem
from tests.helpers import become, rows

VALID = '{"schemas": {"a": {"source": {"type": "bigquery"}, "tables": []}}}'
INVALID = '{"schemas": {"a": {"source": {"type": "bigquery"}, "tables": [{"strategy": "x"}]}}}'


async def insert(connection: psycopg.AsyncConnection, document: str) -> int:
    """Insert one unvalidated draft as the table owner and return its id."""
    cursor = await connection.execute(
        b"INSERT INTO data_proxy.revisions (author, document) VALUES ('ana', %s::jsonb) RETURNING id",
        (document,),
    )
    row = await cursor.fetchone()
    assert row is not None
    identifier = row[0]
    assert isinstance(identifier, int)
    return identifier


@pytest.mark.postgres
class TestRevisionRecords:
    """The worker reads a draft and records the result of its validation."""

    async def test_a_missing_revision_has_no_document(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Reading an unknown id gives nothing."""
        assert await read_revision(Postgres(connection=admin_db), 0) is None

    async def test_a_valid_result_is_recorded(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A draft without problems becomes valid and keeps no errors."""
        pg = Postgres(connection=admin_db)
        identifier = await insert(admin_db, VALID)
        document = await read_revision(pg, identifier)
        assert document is not None

        recorded = await write_validation(pg, identifier, document, [])

        result = await rows(
            admin_db,
            f"SELECT valid, errors, validated_at IS NOT NULL FROM admin.revisions WHERE id = {identifier}",
        )
        assert (recorded, result) == (True, [(True, None, True)])

    async def test_problems_are_recorded_as_errors(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A draft with problems becomes invalid and lists them."""
        pg = Postgres(connection=admin_db)
        identifier = await insert(admin_db, INVALID)
        document = await read_revision(pg, identifier)
        assert document is not None

        await write_validation(
            pg, identifier, document, [Problem(pointer="/schemas/a", message="bad")]
        )

        result = await rows(
            admin_db,
            f"SELECT valid, errors FROM admin.revisions WHERE id = {identifier}",
        )
        assert result == [(False, [{"pointer": "/schemas/a", "message": "bad"}])]

    async def test_a_result_for_an_old_document_is_dropped(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """If the editor changed the draft meanwhile, the old result doesn't apply."""
        pg = Postgres(connection=admin_db)
        identifier = await insert(admin_db, VALID)
        old = await read_revision(pg, identifier)
        assert old is not None
        await admin_db.execute(
            b"UPDATE data_proxy.revisions SET document = %s WHERE id = %s",
            (Jsonb({"schemas": {}}), identifier),
        )

        recorded = await write_validation(pg, identifier, old, [])

        result = await rows(
            admin_db, f"SELECT valid FROM admin.revisions WHERE id = {identifier}"
        )
        assert (recorded, result) == (False, [(None,)])


@pytest.mark.postgres
class TestWorkerValidatesDrafts:
    """A real worker validates every draft that an editor saves."""

    @pytest.mark.parametrize(
        ("document", "valid", "pointers"),
        [
            pytest.param(VALID, True, None, id="valid"),
            pytest.param(INVALID, False, ["/schemas/a/tables/0"], id="invalid"),
        ],
    )
    async def test_the_result_appears_on_the_draft(
        self,
        admin_db: psycopg.AsyncConnection,
        sync_worker: object,
        document: str,
        valid: bool,
        pointers: list[str] | None,
    ) -> None:
        """The editor saves, the queue carries the job, and the draft gets its result."""
        await become(admin_db, "console_editor")
        await admin_db.execute(
            b"INSERT INTO admin.drafts (comment, document) VALUES ('chain', %s::jsonb)",
            (document,),
        )
        await admin_db.commit()

        try:
            result: list[tuple[object, ...]] = []
            for _ in range(100):
                result = await rows(
                    admin_db,
                    "SELECT valid, (SELECT jsonb_agg(e ->> 'pointer') FROM jsonb_array_elements(errors) AS e) FROM admin.drafts WHERE comment = 'chain'",
                )
                await admin_db.commit()
                if result and result[0][0] is not None:
                    break
                await asyncio.sleep(0.1)

            assert result == [(valid, pointers)]
        finally:
            await admin_db.execute(b"RESET ROLE")
            await admin_db.execute(b"DELETE FROM data_proxy.revisions")
            await admin_db.execute(
                b"DELETE FROM dbos.workflow_status WHERE name = 'validate_revision'"
            )
            await admin_db.commit()

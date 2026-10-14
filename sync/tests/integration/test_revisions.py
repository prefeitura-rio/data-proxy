import json

import psycopg
import pytest
from psycopg.types.json import Jsonb

from sync.postgres import Postgres
from sync.revisions import read_active_config

DOCUMENT: dict[str, object] = {
    "schemas": {"a": {"source": {"type": "bigquery"}, "tables": []}}
}


@pytest.mark.postgres
class TestReadActiveConfig:
    """The sync reads the document of the active revision, and nothing else."""

    async def test_returns_the_active_revision_with_its_id(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A draft and an old revision don't count."""
        await admin_db.execute(b"DELETE FROM data_proxy.revisions")
        await admin_db.execute(
            b"""
            INSERT INTO data_proxy.revisions (author, document, valid, active, activated_at)
            VALUES ('ana', '{"schemas": {}}', TRUE, FALSE, now())
            """
        )
        cursor = await admin_db.execute(
            b"""
            INSERT INTO data_proxy.revisions (author, document, valid, active, activated_at)
            VALUES ('ana', %s, TRUE, TRUE, now()) RETURNING id
            """,
            (Jsonb(DOCUMENT),),
        )
        row = await cursor.fetchone()
        assert row is not None
        await admin_db.execute(
            b"INSERT INTO data_proxy.revisions (author, document) VALUES ('ana', '{\"schemas\": {\"draft\": {}}}')"
        )

        loaded = await read_active_config(Postgres(connection=admin_db))

        assert loaded is not None
        assert (loaded.revision, json.loads(loaded.document)) == (str(row[0]), DOCUMENT)

    async def test_returns_nothing_while_no_revision_is_active(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Without an active revision the caller falls back to the file."""
        await admin_db.execute(b"DELETE FROM data_proxy.revisions")

        assert await read_active_config(Postgres(connection=admin_db)) is None

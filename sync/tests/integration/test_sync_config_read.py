import json

import psycopg
import pytest

from sync.postgres import Postgres
from sync.state import read_sync_config


@pytest.mark.postgres
class TestReadSyncConfig:
    """The sync reads the configuration from the endpoint function of the database."""

    async def test_returns_the_document_of_the_tables(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """The text is the sync.json that the tables describe."""
        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")

        document = await read_sync_config(Postgres(connection=admin_db))

        assert document is not None
        assert json.loads(document) == {
            "schemas": {"alpha": {"source": {"type": "bigquery"}, "tables": []}}
        }

    async def test_returns_nothing_while_the_tables_are_empty(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Without a schema the caller falls back to the file."""
        assert await read_sync_config(Postgres(connection=admin_db)) is None

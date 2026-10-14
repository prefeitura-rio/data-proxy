from json import loads

import pytest
from psycopg import AsyncConnection

from sync.postgres import Postgres
from sync.state import read_sync_config


@pytest.mark.postgres
class TestReadSyncConfig:
    """The sync reads the configuration from the endpoint function of the database."""

    async def test_returns_the_document_of_the_tables(
        self, admin_db: AsyncConnection
    ) -> None:
        """The text is the sync.json that the tables describe."""
        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")

        document = await read_sync_config(Postgres(connection=admin_db))

        assert loads(document) == {
            "schemas": {"alpha": {"source": {"type": "bigquery"}, "tables": []}}
        }

    async def test_returns_an_empty_configuration_while_the_tables_are_empty(
        self, admin_db: AsyncConnection
    ) -> None:
        """Without a schema there is nothing to sync."""
        document = await read_sync_config(Postgres(connection=admin_db))

        assert loads(document) == {"schemas": {}}

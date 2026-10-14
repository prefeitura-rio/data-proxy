from unittest.mock import AsyncMock

import psycopg
import pytest

from engine.ducklake import (
    DuckLakePaths,
    reader_snapshot,
)
from engine.postgres import Postgres
from engine.settings import settings


@pytest.mark.parametrize("schema", ["app", "bigquery_a"])
def test_each_schema_gets_its_own_metadata_schema_in_the_catalog_database(
    schema: str,
) -> None:
    """Point every schema at the catalog database, with the schema name as the metadata schema."""
    paths = DuckLakePaths.for_schema(schema)

    assert paths == DuckLakePaths(
        catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL}",
        data=f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        metadata_schema=schema,
    )


class TestReaderSnapshot:
    """reader_snapshot behavior tests."""

    async def test_returns_snapshot_from_reader_function(self) -> None:
        """Returns snapshot from reader function."""
        pg_conn = AsyncMock(spec=Postgres)
        pg_conn.query.return_value = [(7,)]

        assert await reader_snapshot(pg_conn, "app") == 7
        pg_conn.commit.assert_awaited_once()

    async def test_returns_none_when_reader_catalog_is_missing(self) -> None:
        """Returns none when reader catalog is missing."""
        pg_conn = AsyncMock(spec=Postgres)
        pg_conn.query.side_effect = psycopg.errors.InternalError_("no catalog")

        assert await reader_snapshot(pg_conn, "app") is None
        pg_conn.rollback.assert_awaited_once()

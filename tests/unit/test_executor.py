"""Tests for SQL execution ports and adapters."""

from typing import cast
from unittest.mock import patch

import pytest
from google.cloud.bigquery import Client, QueryJobConfig
from psycopg import AsyncConnection, AsyncCursor

from data_proxy.bigquery.clients import BigQuery
from data_proxy.duckdb import DuckDB
from data_proxy.executor import execute, execute_postgres_cursor, execute_sql
from tests.fixtures.unit import RecordingCursor


class TestPostgresExecution:
    """PostgresExecution behavior tests."""

    @pytest.mark.asyncio
    async def test_rejects_many_rows_on_connection(
        self, async_connection_double: AsyncConnection
    ) -> None:
        """Reject many-row execution on a connection."""
        connection = cast(AsyncCursor, cast(object, async_connection_double))
        with (
            patch("data_proxy.executor.render_template", return_value="SELECT 1"),
            pytest.raises(TypeError, match="cursor"),
        ):
            await execute_sql(connection, "postgres/table_exists", params=[("x",)])

    @pytest.mark.asyncio
    async def test_executes_many_rows_on_cursor(
        self, recording_cursor: RecordingCursor
    ) -> None:
        """Execute many rows on a cursor."""
        cursor = recording_cursor
        await execute_postgres_cursor(
            cast("AsyncCursor", cast(object, cursor)), "SELECT 1", params=[("x",)]
        )
        assert cursor.operations == [("executemany", ("SELECT 1", [("x",)]))]

    @pytest.mark.asyncio
    async def test_executes_one_statement_on_cursor(
        self, recording_cursor: RecordingCursor
    ) -> None:
        """Execute one statement on a cursor."""
        cursor = recording_cursor
        await execute_postgres_cursor(
            cast("AsyncCursor", cast(object, cursor)), "SELECT 1"
        )
        assert cursor.operations == [("execute", ("SELECT 1", None))]


class TestExecutionValidation:
    """ExecutionValidation behavior tests."""

    @pytest.mark.asyncio
    async def test_rejects_unsupported_connection_type(self) -> None:
        """Reject an unsupported connection type."""
        with pytest.raises(TypeError, match="unsupported"):
            await execute(object(), "SELECT 1")

    @pytest.mark.asyncio
    async def test_rejects_job_config_for_duckdb(self, duckdb: DuckDB) -> None:
        """Reject a BigQuery job configuration for DuckDB."""
        with pytest.raises(TypeError, match="job_config"):
            await execute(duckdb, "SELECT 1", job_config=QueryJobConfig())

    @pytest.mark.asyncio
    async def test_rejects_parameters_for_bigquery(self) -> None:
        """Reject positional parameters for BigQuery."""
        with pytest.raises(TypeError, match="params"):
            await execute(
                BigQuery(client=cast(Client, object())),
                "SELECT 1",
                params=("unexpected",),
                job_config=QueryJobConfig(),
            )

    @pytest.mark.asyncio
    async def test_requires_job_config_for_bigquery(self) -> None:
        """Require a job configuration for BigQuery."""
        with pytest.raises(TypeError, match="job_config"):
            await execute(BigQuery(client=cast(Client, object())), "SELECT 1")

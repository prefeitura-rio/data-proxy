"""Tests for SQL execution ports and adapters."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.cloud.bigquery import Client, QueryJobConfig
from psycopg import AsyncConnection

from dp.bigquery.clients import BigQuery
from dp.duckdb import DuckDB
from dp.executor import (
    execute,
    execute_postgres_cursor,
    execute_sql,
)


@pytest.mark.asyncio
async def test_postgres_connection_executes_a_statement() -> None:
    """
    GIVEN: an async PostgreSQL connection.
    WHEN: a template is executed.
    THEN: the rendered statement is sent through the connection.
    """
    connection = AsyncMock(spec=AsyncConnection)

    with patch("dp.executor.render_template", return_value="SELECT 1"):
        await execute_sql(connection, "postgres/table_exists")

    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgres_connection_rejects_executemany() -> None:
    """
    GIVEN: an async PostgreSQL connection.
    WHEN: a list of parameter rows is supplied.
    THEN: execution is rejected because executemany needs a cursor.
    """
    connection = AsyncMock(spec=AsyncConnection)

    with (
        patch("dp.executor.render_template", return_value="SELECT 1"),
        pytest.raises(TypeError, match="cursor"),
    ):
        await execute_sql(connection, "postgres/table_exists", params=[("x",)])


@pytest.mark.asyncio
async def test_postgres_cursor_executes_many_rows() -> None:
    """
    GIVEN: an async PostgreSQL cursor.
    WHEN: a list of parameter rows is supplied.
    THEN: executemany is used.
    """
    cursor = AsyncMock()

    with patch("dp.executor.render_template", return_value="SELECT 1"):
        await execute_postgres_cursor(cursor, "SELECT 1", params=[("x",)])

    cursor.executemany.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgres_cursor_executes_one_statement() -> None:
    """
    GIVEN: an async PostgreSQL cursor.
    WHEN: no parameter rows are supplied.
    THEN: a single execute is used.
    """
    cursor = AsyncMock()
    await execute_postgres_cursor(cursor, "SELECT 1")
    cursor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsupported_connection_is_rejected() -> None:
    """
    GIVEN: an object that is not a registered connection.
    WHEN: execution is requested.
    THEN: a TypeError names the unsupported type.
    """
    with pytest.raises(TypeError, match="unsupported"):
        await execute(object(), "SELECT 1")


@pytest.mark.asyncio
async def test_duckdb_template_returns_rows(duckdb: DuckDB) -> None:
    """
    GIVEN: a DuckDB facade.
    WHEN: a template is executed.
    THEN: the backend returns every row instead of a synchronous cursor.
    """
    with patch("dp.executor.render_template", return_value="SELECT 1"):
        rows = await execute_sql(duckdb, "duckdb/describe_table")

    assert rows == [(1,)]


@pytest.mark.asyncio
async def test_duckdb_rejects_job_config(duckdb: DuckDB) -> None:
    """
    GIVEN: a DuckDB facade.
    WHEN: a BigQuery job configuration is supplied.
    THEN: execution is rejected.
    """
    with pytest.raises(TypeError, match="job_config"):
        await execute(duckdb, "SELECT 1", job_config=QueryJobConfig())


@pytest.mark.asyncio
async def test_bigquery_template_returns_rows() -> None:
    """
    GIVEN: a BigQuery facade.
    WHEN: a template is executed with a job configuration.
    THEN: the materialized rows are returned.
    """
    client = MagicMock(spec=Client)
    job = MagicMock()
    job.result.return_value = [{"a": 1}]
    client.query.return_value = job

    with patch("dp.executor.render_template", return_value="SELECT 1"):
        rows = await execute_sql(
            BigQuery(client=client), "bigquery/partitions", job_config=QueryJobConfig()
        )

    assert list(rows) == [{"a": 1}]


@pytest.mark.asyncio
async def test_bigquery_rejects_params() -> None:
    """
    GIVEN: a BigQuery facade.
    WHEN: parameter rows are supplied.
    THEN: execution is rejected.
    """
    with pytest.raises(TypeError, match="params"):
        await execute(
            BigQuery(client=MagicMock(spec=Client)),
            "SELECT 1",
            params=("unexpected",),
            job_config=QueryJobConfig(),
        )


@pytest.mark.asyncio
async def test_bigquery_requires_job_config() -> None:
    """
    GIVEN: a BigQuery facade.
    WHEN: no job configuration is supplied.
    THEN: execution is rejected.
    """
    with pytest.raises(TypeError, match="job_config"):
        await execute(BigQuery(client=MagicMock(spec=Client)), "SELECT 1")

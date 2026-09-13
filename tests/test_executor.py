"""Tests for SQL execution ports and adapters."""

from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest
from google.cloud.bigquery import Client, QueryJobConfig
from psycopg import AsyncConnection

from dp.executor import (
    execute,
    execute_duckdb,
    execute_postgres_connection,
    execute_postgres_cursor,
    execute_sql,
)


@pytest.mark.asyncio
async def test_postgres_connection_executes_a_statement() -> None:
    connection = AsyncMock(spec=AsyncConnection)

    with patch("dp.executor.render_template", return_value="SELECT 1"):
        await execute_sql(connection, "postgres/table_exists")

    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgres_connection_rejects_executemany() -> None:
    connection = AsyncMock(spec=AsyncConnection)

    with (
        patch("dp.executor.render_template", return_value="SELECT 1"),
        pytest.raises(TypeError, match="cursor"),
    ):
        await execute_sql(connection, "postgres/table_exists", params=[("x",)])


@pytest.mark.asyncio
async def test_postgres_cursor_executes_many_rows() -> None:
    cursor = AsyncMock()

    with patch("dp.executor.render_template", return_value="SELECT 1"):
        await execute_postgres_cursor(cursor, "SELECT 1", params=[("x",)])

    cursor.executemany.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgres_connection_rejects_job_config() -> None:
    with pytest.raises(TypeError, match="job_config"):
        await execute_postgres_connection(
            AsyncMock(spec=AsyncConnection), "SELECT 1", job_config=QueryJobConfig()
        )


@pytest.mark.asyncio
async def test_postgres_cursor_rejects_job_config() -> None:
    with pytest.raises(TypeError, match="job_config"):
        await execute_postgres_cursor(
            AsyncMock(), "SELECT 1", job_config=QueryJobConfig()
        )


@pytest.mark.asyncio
async def test_postgres_cursor_executes_one_statement() -> None:
    cursor = AsyncMock()
    await execute_postgres_cursor(cursor, "SELECT 1")
    cursor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_duckdb_rejects_job_config() -> None:
    connection = duckdb.connect(":memory:")
    try:
        with pytest.raises(TypeError, match="job_config"):
            await execute_duckdb(connection, "SELECT 1", job_config=QueryJobConfig())
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_duckdb_execution_is_offloaded() -> None:
    connection = duckdb.connect(":memory:")
    try:
        with patch("dp.executor.render_template", return_value="SELECT 1"):
            result = await execute_sql(connection, "duckdb/setup")
        assert result is connection
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_bigquery_execution_submits_a_job() -> None:
    client = MagicMock(spec=Client)
    job = MagicMock()
    client.query.return_value = job

    with patch("dp.executor.render_template", return_value="SELECT 1"):
        result = await execute_sql(
            client,
            "bigquery/partitions",
            job_config=QueryJobConfig(),
        )

    assert result is job
    client.query.assert_called_once()


@pytest.mark.asyncio
async def test_bigquery_execution_rejects_params() -> None:
    client = MagicMock(spec=Client)

    with (
        patch("dp.executor.render_template", return_value="SELECT 1"),
        pytest.raises(TypeError, match="params"),
    ):
        await execute(
            client,
            "SELECT 1",
            params=("unexpected",),
            job_config=QueryJobConfig(),
        )


@pytest.mark.asyncio
async def test_bigquery_execution_requires_configuration() -> None:
    client = MagicMock(spec=Client)

    with (
        patch("dp.executor.render_template", return_value="SELECT 1"),
        pytest.raises(TypeError, match="job_config"),
    ):
        await execute(client, "SELECT 1")


@pytest.mark.asyncio
async def test_unsupported_connection_is_rejected() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        await execute(object(), "SELECT 1")

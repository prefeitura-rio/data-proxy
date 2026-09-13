"""Ports and singledispatch facade for rendered SQL execution."""

from collections.abc import Mapping
from datetime import datetime
from functools import singledispatch
from typing import LiteralString, Protocol, cast, overload

from asyncer import asyncify
from duckdb import DuckDBPyConnection
from google.cloud.bigquery import Client, QueryJobConfig
from psycopg import AsyncConnection, AsyncCursor
from psycopg.sql import Composable
from whenever import Instant

from .templates import render_template

type SQLParam = str | datetime | Instant | None


class BigQueryJob(Protocol):
    """Minimal BigQuery job port."""

    def result(self) -> object: ...


@singledispatch
async def execute(
    connection: object,
    sql: str,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
    job_config: QueryJobConfig | None = None,
) -> object:
    """Execute SQL through a registered backend implementation."""
    raise TypeError(f"unsupported SQL connection: {type(connection).__name__}")


@execute.register
async def execute_postgres_connection(
    connection: AsyncConnection,
    sql: str,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
    job_config: QueryJobConfig | None = None,
) -> AsyncCursor:
    """Execute one statement through an async PostgreSQL connection."""
    if isinstance(params, list):
        raise TypeError("executemany requires a cursor, not a connection")
    if job_config is not None:
        raise TypeError("PostgreSQL execution does not accept job_config")
    return await connection.execute(cast(LiteralString, sql), params=params)


@execute.register
async def execute_postgres_cursor(
    connection: AsyncCursor,
    sql: str,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
    job_config: QueryJobConfig | None = None,
) -> AsyncCursor | None:
    """Execute one or many statements through an async PostgreSQL cursor."""
    if job_config is not None:
        raise TypeError("PostgreSQL execution does not accept job_config")
    if isinstance(params, list):
        return await connection.executemany(cast(LiteralString, sql), params)
    return await connection.execute(cast(LiteralString, sql), params=params)


@execute.register
async def execute_duckdb(
    connection: DuckDBPyConnection,
    sql: str,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
    job_config: QueryJobConfig | None = None,
) -> DuckDBPyConnection:
    """Execute SQL through DuckDB in a worker thread."""
    if job_config is not None:
        raise TypeError("DuckDB execution does not accept job_config")
    return await asyncify(connection.execute)(sql, *(params or ()))


@execute.register(Client)
async def execute_bigquery(
    connection: Client,
    sql: str,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
    job_config: QueryJobConfig | None = None,
) -> BigQueryJob:
    """Submit SQL to BigQuery in a worker thread."""
    if params is not None:
        raise TypeError("BigQuery execution does not accept params")
    if job_config is None:
        raise TypeError("BigQuery execution requires job_config")
    return await asyncify(connection.query)(sql, job_config=job_config)


@overload
async def execute_sql(
    connection: AsyncCursor,
    path: str,
    mapping: Mapping[str, str | Composable] | None = None,
    *,
    params: list[tuple[SQLParam, ...]],
) -> None: ...


@overload
async def execute_sql(
    connection: AsyncConnection | AsyncCursor,
    path: str,
    mapping: Mapping[str, str | Composable] | None = None,
    *,
    params: tuple[SQLParam, ...] | None = None,
) -> AsyncCursor: ...


@overload
async def execute_sql(
    connection: DuckDBPyConnection,
    path: str,
    mapping: Mapping[str, str | Composable] | None = None,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
) -> DuckDBPyConnection: ...


@overload
async def execute_sql(
    connection: Client,
    path: str,
    mapping: Mapping[str, str | Composable] | None = None,
    *,
    job_config: QueryJobConfig,
) -> BigQueryJob: ...


async def execute_sql(
    connection: object,
    path: str,
    mapping: Mapping[str, str | Composable] | None = None,
    *,
    params: list[tuple[SQLParam, ...]] | tuple[SQLParam, ...] | None = None,
    job_config: QueryJobConfig | None = None,
) -> object:
    """Render and execute one SQL template through a registered backend."""
    sql = render_template(path, mapping or {})
    return await execute(
        connection,
        sql,
        params=params,
        job_config=job_config,
    )

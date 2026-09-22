"""Ports and singledispatch facade for rendered SQL execution."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import singledispatch
from typing import LiteralString, cast, overload

from google.cloud.bigquery import QueryJobConfig
from google.cloud.bigquery.table import Row
from psycopg import AsyncConnection, AsyncCursor
from whenever import Instant

from .bigquery.clients import BigQuery
from .duckdb import DuckDB
from .templates import render_template
from .types import TemplateValue

type SQLParam = str | datetime | Instant | None
type SQLRow = tuple[SQLParam, ...]
type SQLParams = Sequence[SQLParam] | list[SQLRow] | dict[str, SQLParam] | None


@singledispatch
async def execute(
    conn: object,
    sql: str,
    *,
    params: SQLParams = None,
    job_config: QueryJobConfig | None = None,
) -> object:
    """Execute SQL through a registered backend implementation."""
    raise TypeError(f"unsupported SQL connection: {type(conn).__name__}")


@execute.register
async def execute_postgres_connection(
    conn: AsyncConnection,
    sql: str,
    *,
    params: SQLRow | list[SQLRow] | dict[str, SQLParam] | None = None,
    job_config: QueryJobConfig | None = None,
) -> AsyncCursor:
    """Execute one statement through an async PostgreSQL connection."""
    match params:
        case list():
            raise TypeError("executemany requires a cursor, not a connection")
        case _:
            pass

    if job_config is not None:
        raise TypeError("PostgreSQL execution does not accept job_config")

    return await conn.execute(cast(LiteralString, sql), params=params)


@execute.register
async def execute_postgres_cursor(
    conn: AsyncCursor,
    sql: str,
    *,
    params: SQLRow | list[SQLRow] | dict[str, SQLParam] | None = None,
    job_config: QueryJobConfig | None = None,
) -> AsyncCursor | None:
    """Execute one or many statements through an async PostgreSQL cursor."""
    if job_config is not None:
        raise TypeError("PostgreSQL execution does not accept job_config")

    match params:
        case list():
            return await conn.executemany(cast(LiteralString, sql), params)
        case _:
            return await conn.execute(cast(LiteralString, sql), params=params)


@execute.register
async def execute_duckdb(
    conn: DuckDB,
    sql: str,
    *,
    params: Sequence[SQLParam] | None = None,
    job_config: QueryJobConfig | None = None,
) -> list[tuple[object, ...]]:
    """Execute one query through DuckDB and return every row."""
    if job_config is not None:
        raise TypeError("DuckDB execution does not accept job_config")

    return await conn.fetchall(sql, params)


@execute.register
async def execute_bigquery(
    conn: BigQuery,
    sql: str,
    *,
    params: SQLParams = None,
    job_config: QueryJobConfig | None = None,
) -> Sequence[Row]:
    """Execute one query through BigQuery and return every row."""
    if params is not None:
        raise TypeError("BigQuery execution does not accept params")

    if job_config is None:
        raise TypeError("BigQuery execution requires job_config")

    return await conn.rows(sql, job_config)


@overload
async def execute_sql(
    conn: AsyncConnection | AsyncCursor,
    path: str,
    mapping: Mapping[str, TemplateValue] | None = None,
    *,
    params: dict[str, SQLParam] | None = None,
) -> AsyncCursor: ...


@overload
async def execute_sql(
    conn: AsyncCursor,
    path: str,
    mapping: Mapping[str, TemplateValue] | None = None,
    *,
    params: list[SQLRow],
) -> None: ...


@overload
async def execute_sql(
    conn: AsyncConnection | AsyncCursor,
    path: str,
    mapping: Mapping[str, TemplateValue] | None = None,
    *,
    params: SQLRow | None = None,
) -> AsyncCursor: ...


@overload
async def execute_sql(
    conn: DuckDB,
    path: str,
    mapping: Mapping[str, TemplateValue] | None = None,
    *,
    params: Sequence[SQLParam] | None = None,
) -> list[tuple[object, ...]]: ...


@overload
async def execute_sql(
    conn: BigQuery,
    path: str,
    mapping: Mapping[str, TemplateValue] | None = None,
    *,
    job_config: QueryJobConfig,
) -> Sequence[Row]: ...


async def execute_sql(
    conn: AsyncConnection | AsyncCursor | DuckDB | BigQuery,
    path: str,
    mapping: Mapping[str, TemplateValue] | None = None,
    *,
    params: SQLParams = None,
    job_config: QueryJobConfig | None = None,
) -> object:
    """Render and execute one SQL template through a registered backend."""
    sql = render_template(path, mapping or {})
    return await execute(conn, sql, params=params, job_config=job_config)

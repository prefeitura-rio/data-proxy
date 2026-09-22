"""Tests for the async DuckDB facade."""

import asyncio

import pytest

from dp.duckdb import DuckDB


@pytest.mark.asyncio
async def test_connect_registers_s3_secrets() -> None:
    """
    GIVEN: an empty DuckDB facade.
    WHEN: connect is entered.
    THEN: the S3 setup statement runs and the connection is usable.
    """
    async with DuckDB.connect() as duckdb:
        rows = await duckdb.fetchall("SELECT 42")

    assert rows == [(42,)]


@pytest.mark.asyncio
async def test_fetchall_runs_statements_and_returns_rows() -> None:
    """
    GIVEN: an open facade.
    WHEN: statements and a query run through fetchall.
    THEN: the statements take effect and the query returns every row.
    """
    async with DuckDB.connect() as duckdb:
        await duckdb.fetchall("CREATE TABLE t (a int)")
        await duckdb.fetchall("INSERT INTO t VALUES (1), (2)")

        rows = await duckdb.fetchall("SELECT * FROM t ORDER BY a")

    assert rows == [(1,), (2,)]


@pytest.mark.asyncio
async def test_fetchall_binds_parameters() -> None:
    """
    GIVEN: a table with two rows.
    WHEN: fetchall runs a parameterized query.
    THEN: only the matching row is returned.
    """
    async with DuckDB.connect() as duckdb:
        await duckdb.fetchall("CREATE TABLE t (a int)")
        await duckdb.fetchall("INSERT INTO t VALUES (1), (2)")

        rows = await duckdb.fetchall("SELECT * FROM t WHERE a = ?", [2])

    assert rows == [(2,)]


@pytest.mark.asyncio
async def test_queries_do_not_block_the_event_loop() -> None:
    """
    GIVEN: an open facade.
    WHEN: a query runs while a ticker task is scheduled.
    THEN: the event loop keeps running, so the query left the loop thread.
    """
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            ticks += 1
            await asyncio.sleep(0.001)

    async with DuckDB.connect() as duckdb:
        ticker_task = asyncio.create_task(ticker())
        await duckdb.fetchall("SELECT count(*) FROM generate_series(1, 300000) t(i)")
        await ticker_task

    assert ticks == 20


@pytest.mark.asyncio
async def test_connect_closes_the_connection() -> None:
    """
    GIVEN: an open facade.
    WHEN: connect exits.
    THEN: the underlying connection is closed.
    """
    async with DuckDB.connect() as duckdb:
        connection = duckdb.connection

    with pytest.raises(Exception, match=r"closed|Connection"):
        connection.execute("SELECT 1")

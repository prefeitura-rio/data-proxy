"""Tests for the Postgres-backed synchronization state repository."""

import psycopg
import pytest

from data_proxy.models import Strategy, TableState
from data_proxy.state import (
    emit_error,
    read_partition_manifest,
    read_table_signature,
    read_table_state,
    write_table_states,
)


@pytest.mark.usefixtures("test_settings")
@pytest.mark.asyncio
async def test_table_state_round_trip(dbos_conn: psycopg.AsyncConnection) -> None:
    """
    GIVEN: an empty data_proxy.state table.
    WHEN: state for one table is written and read back.
    THEN: the read returns the written state.
    """
    procedure = await (
        await dbos_conn.execute(
            "SELECT to_regprocedure('data_proxy.cleanup_table_state(jsonb)')"
        )
    ).fetchone()
    assert procedure is not None
    assert procedure[0] is not None
    state = TableState(strategy=Strategy.FULL, signature="abc")
    await write_table_states(dbos_conn, {"p.d.t": state})
    assert await read_table_state(dbos_conn, "p.d.t") == state
    assert await read_table_signature(dbos_conn, "p.d.t") == "abc"


@pytest.mark.usefixtures("test_settings")
@pytest.mark.asyncio
async def test_table_state_upsert_replaces_existing(
    dbos_conn: psycopg.AsyncConnection,
) -> None:
    """
    GIVEN: state for one table.
    WHEN: new state for the same table is written.
    THEN: the read returns the new state.
    """
    await write_table_states(
        dbos_conn, {"p.d.t": TableState(strategy=Strategy.FULL, signature="old")}
    )
    await write_table_states(
        dbos_conn, {"p.d.t": TableState(strategy=Strategy.FULL, signature="new")}
    )
    assert await read_table_signature(dbos_conn, "p.d.t") == "new"


@pytest.mark.usefixtures("test_settings")
@pytest.mark.asyncio
async def test_read_table_state_returns_none_when_absent(
    dbos_conn: psycopg.AsyncConnection,
) -> None:
    """
    GIVEN: an empty data_proxy.state table.
    WHEN: state for an unknown table is read.
    THEN: None is returned.
    """
    assert await read_table_state(dbos_conn, "p.d.t") is None
    assert await read_table_signature(dbos_conn, "p.d.t") is None
    assert await read_partition_manifest(dbos_conn, "p.d.t") is None


@pytest.mark.usefixtures("test_settings")
@pytest.mark.asyncio
async def test_emit_error_persists_one_row(dbos_conn: psycopg.AsyncConnection) -> None:
    """
    GIVEN: an empty data_proxy.errors table.
    WHEN: one error event is emitted.
    THEN: one row with the reason and fields is persisted.
    """
    await emit_error(dbos_conn, "extraction_failed", table="p.d.t", error="boom")
    cursor = await dbos_conn.execute(
        "SELECT reason, fields FROM data_proxy.errors ORDER BY id DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "extraction_failed"
    assert row[1]["table"] == "p.d.t"
    assert row[1]["error"] == "boom"


@pytest.mark.usefixtures("test_settings")
@pytest.mark.asyncio
async def test_cleanup_table_state_removes_unconfigured_tables(
    dbos_conn: psycopg.AsyncConnection,
) -> None:
    state = TableState(strategy=Strategy.FULL, signature="abc")
    await write_table_states(dbos_conn, {"p.app.stale": state})
    await dbos_conn.execute(
        b"CALL data_proxy.cleanup_table_state(%s::jsonb)", ('{"schemas": {}}',)
    )
    assert await read_table_state(dbos_conn, "p.app.stale") is None

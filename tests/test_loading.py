"""Tests for Parquet-to-PostgreSQL loading operations."""

from typing import cast
from unittest.mock import ANY, patch

import psycopg
import pytest
from helpers import FakeDuckDBConnection, FakePgConn

from dp.loading import (
    apply_sync_plan,
    bootstrap_table,
    load_table,
    prepare_tables,
    publish_prepared_tables,
    publish_table,
    validate_sync_plan,
)
from dp.models import (
    DumpTable,
    IndexConfig,
    RlsConfig,
    SyncConfig,
    SyncPlan,
    WindowTable,
)


def postgres_connection(fake: FakePgConn) -> psycopg.Connection:
    """Cast the PostgreSQL test double to the production connection type."""
    return cast("psycopg.Connection[tuple[object, ...]]", cast(object, fake))


def test_bootstrap_grants_access_without_rls() -> None:
    """A non-RLS table receives its read grant."""
    connection = FakePgConn()

    with patch("dp.loading.load_template", return_value="SELECT 1"):
        bootstrap_table(
            postgres_connection(connection),
            {"schema": "app", "table_name": "table", "rls": None},
        )

    assert connection.execute_calls == 1


def test_bootstrap_enables_rls() -> None:
    """An RLS table renders grants and policy SQL together."""
    connection = FakePgConn()

    with patch("dp.loading.load_template", return_value="SELECT 1"):
        bootstrap_table(
            postgres_connection(connection),
            {
                "schema": "app",
                "table_name": "table",
                "rls": RlsConfig(column="unit_id"),
            },
        )

    assert connection.execute_calls == 1


def test_load_table_uses_exact_paths() -> None:
    """Only explicitly planned Parquet paths are loaded."""
    duckdb = FakeDuckDBConnection()
    paths = ["s3://bucket/table/a.parquet", "s3://bucket/table/b.parquet"]

    with patch("dp.loading.load_template", return_value="SELECT 1"):
        load_table(duckdb, "app", "table__next", paths)

    assert duckdb.executed == ["SELECT 1", "SELECT 1"]


def test_publish_table_swaps_before_index_creation() -> None:
    """A prepared table is bootstrapped, swapped, and indexed in order."""
    connection = FakePgConn()
    table = DumpTable(
        bq_table="p.app.table",
        indexes=[IndexConfig(name="idx_table", columns=["id"])],
    )

    def template_name(name: str, mapping: dict[str, str]) -> str:
        """Return the template name for operation-order assertions."""
        return name

    with patch("dp.loading.load_template", side_effect=template_name):
        publish_table(postgres_connection(connection), table)

    assert connection.executed == [
        b"pg/grant_select",
        b"pg/swap_table",
        b"pg/create_index",
    ]


def test_validate_sync_plan_rejects_unknown_table() -> None:
    """A plan cannot publish a table absent from the mounted config."""
    config = SyncConfig(tables=[DumpTable(bq_table="p.app.known")])
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.app.unknown": "100"},
        paths={"p.app.unknown": ["s3://bucket/unknown/data.parquet"]},
    )

    with pytest.raises(RuntimeError, match="unknown tables"):
        validate_sync_plan(config, plan)


def test_prepare_tables_uses_exact_planned_paths() -> None:
    """Preparation loads only planned tables and their exact paths."""
    config = SyncConfig(
        tables=[
            DumpTable(bq_table="p.app.changed"),
            DumpTable(bq_table="p.app.unchanged"),
        ]
    )
    path = "s3://bucket/changed/data.parquet"
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.app.changed": "100"},
        paths={"p.app.changed": [path]},
    )
    duckdb = FakeDuckDBConnection()

    with (
        patch("dp.loading.load_template", return_value="SELECT 1"),
        patch("dp.loading.load_table") as load,
    ):
        prepared = prepare_tables(duckdb, config, plan, {"p.app.changed"})

    load.assert_called_once_with(ANY, "app", "changed__next", [path])
    assert [table.bq_table for table in prepared] == ["p.app.changed"]


def test_publish_prepared_tables_swaps_each_table() -> None:
    """Every prepared shadow table is atomically published."""
    connection = FakePgConn()
    tables: list[DumpTable | WindowTable] = [
        DumpTable(bq_table="p.app.one"),
        DumpTable(bq_table="p.app.two"),
    ]

    with patch("dp.loading.publish_table") as publish:
        publish_prepared_tables(postgres_connection(connection), tables)

    assert publish.call_count == 2


def test_apply_sync_plan_delegates_all_steps() -> None:
    """The orchestrator receives connections and runs every step."""
    config = SyncConfig(tables=[DumpTable(bq_table="p.app.changed")])
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.app.changed": "100"},
        paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
    )
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()

    with (
        patch("dp.loading.initialize_schemas") as initialize,
        patch("dp.loading.prepare_tables", return_value=[config.tables[0]]),
        patch("dp.loading.publish_prepared_tables") as publish,
        patch("dp.loading.reload_postgrest") as reload,
    ):
        apply_sync_plan(pg_conn, duckdb, config, plan)

    initialize.assert_called_once()
    publish.assert_called_once()
    reload.assert_called_once()

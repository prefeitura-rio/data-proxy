"""Tests for Parquet-to-PostgreSQL loading operations."""

from unittest.mock import ANY, patch

import pytest
from helpers import FakeDuckDBConnection, FakePgConn, postgres_connection
from psycopg.sql import Composable

from dp.loading import (
    apply_sync_plan,
    bootstrap_table,
    create_incremental_shadow,
    initialize_schemas,
    load_table,
    partition_predicate,
    prepare_tables,
    publish_prepared_tables,
    publish_table,
    reload_postgrest,
    validate_sync_plan,
)
from dp.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    RlsConfig,
    SyncConfig,
    SyncPlan,
    TimeRangeSelection,
)
from dp.templates import TemplateSpec


def template_name(spec: TemplateSpec) -> str:
    """Return the template path for operation-order assertions."""
    return spec["path"]


def physical_partition(partition_id: str) -> PhysicalPartition:
    """Return one normalized range for loading tests."""
    lower = int(partition_id)
    return PhysicalPartition(
        partition_id=partition_id,
        signature="signature",
        selection=RangeSelection(
            partition_id=partition_id, column="cpf", lower=lower, upper=lower + 10
        ),
    )


def test_create_incremental_shadow_excludes_affected_ranges() -> None:
    """Shadow preparation copies only rows outside changed physical bounds."""
    connection = FakePgConn()
    rendered: list[TemplateSpec] = []

    def render(spec: TemplateSpec) -> str:
        rendered.append(spec)
        return "SELECT 1"

    with patch("dp.loading.load_template", side_effect=render):
        create_incremental_shadow(
            postgres_connection(connection),
            PartitionedTable(name="p.app.people"),
            [physical_partition("10"), physical_partition("20")],
        )

    assert [template_name(spec) for spec in rendered] == [
        "pg/partition_range_predicate",
        "pg/partition_range_predicate",
        "pg/prepare_incremental_table",
    ]
    predicate = rendered[-1]["mapping"]["affected_partitions"]
    assert isinstance(predicate, Composable)


def test_partition_predicate_matches_remainder_rows_outside_the_range() -> None:
    """A remainder partition's predicate matches null and out-of-range rows."""
    remainder = PhysicalPartition(
        partition_id="__NULL__",
        signature="signature",
        selection=RemainderSelection(column="cpf", start=0, end=100),
    )

    rendered = partition_predicate(remainder).as_string(None)

    assert '"cpf" IS NULL' in rendered
    assert '"cpf" < 0' in rendered
    assert '"cpf" >= 100' in rendered


def test_partition_predicate_matches_bounded_range_rows() -> None:
    """An ordinary partition's predicate matches its [lower, upper) bounds."""
    bounded = PhysicalPartition(
        partition_id="10",
        signature="signature",
        selection=RangeSelection(partition_id="10", column="cpf", lower=10, upper=20),
    )

    rendered = partition_predicate(bounded).as_string(None)

    assert '"cpf" >= 10' in rendered
    assert '"cpf" < 20' in rendered


def test_partition_predicate_matches_time_range_rows() -> None:
    """A time partition's predicate matches its [lower, upper) date bounds."""
    value = PhysicalPartition(
        partition_id="20250101",
        signature="signature",
        selection=TimeRangeSelection(
            column="dt", lower="2025-01-01", upper="2025-01-02"
        ),
    )

    rendered = partition_predicate(value).as_string(None)

    assert "\"dt\" >= '2025-01-01'" in rendered
    assert "\"dt\" < '2025-01-02'" in rendered


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
    table = FullTable(
        name="p.app.table",
        indexes=[IndexConfig(name="idx_table", columns=["id"])],
    )

    with patch("dp.loading.load_template", side_effect=template_name):
        publish_table(postgres_connection(connection), table)

    assert connection.executed == [
        b"pg/swap_table",
        b"pg/create_index",
    ]


def test_initialize_schemas_creates_roles_then_schemas() -> None:
    """Roles are created once, then every configured schema, then committed."""
    connection = FakePgConn()
    config = SyncConfig(
        tables=[FullTable(name="p.app.one"), FullTable(name="p.other.two")]
    )

    with patch("dp.loading.load_template", side_effect=template_name):
        initialize_schemas(postgres_connection(connection), config)

    assert connection.executed[0] == b"pg/init_roles"
    assert connection.executed[1:] == [b"pg/init_schema", b"pg/init_schema"]


def test_reload_postgrest_revokes_then_notifies() -> None:
    """Anonymous access is revoked per schema before the reload notification."""
    connection = FakePgConn()
    config = SyncConfig(tables=[FullTable(name="p.app.one")])

    with patch("dp.loading.load_template", return_value="SELECT 1"):
        reload_postgrest(postgres_connection(connection), config)

    assert connection.executed == [
        b"SELECT 1",
        b"NOTIFY pgrst, 'reload schema'",
    ]


def test_prepare_tables_skips_table_with_missing_paths() -> None:
    """A changed table absent from the plan's paths is logged and skipped."""
    config = SyncConfig(tables=[FullTable(name="p.app.changed")])
    plan = SyncPlan(sync_id="s1", signatures={}, paths={})
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()

    prepared = prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

    assert prepared == []


def test_validate_sync_plan_rejects_unknown_table() -> None:
    """A plan cannot publish a table absent from the mounted config."""
    config = SyncConfig(tables=[FullTable(name="p.app.known")])
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
            FullTable(name="p.app.changed"),
            FullTable(name="p.app.unchanged"),
        ]
    )
    path = "s3://bucket/changed/data.parquet"
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.app.changed": "100"},
        paths={"p.app.changed": [path]},
    )
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()

    with (
        patch("dp.loading.load_template", return_value="SELECT 1"),
        patch("dp.loading.bootstrap_table") as bootstrap,
        patch("dp.loading.load_table") as load,
    ):
        prepared = prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

    bootstrap.assert_called_once_with(
        pg_conn,
        {"schema": "app", "table_name": "changed__next", "rls": None},
    )
    load.assert_called_once_with(ANY, "app", "changed__next", [path])
    assert [table.name for table in prepared] == ["p.app.changed"]


def test_apply_sync_plan_publishes_other_tables_after_a_load_failure() -> None:
    """A failed load skips only its own table, not the whole synchronization."""
    config = SyncConfig(
        tables=[
            FullTable(name="p.app.first"),
            FullTable(name="p.app.second"),
        ]
    )
    plan = SyncPlan(
        sync_id="s1",
        signatures={
            "p.app.first": "1",
            "p.app.second": "2",
        },
        paths={
            "p.app.first": ["s3://bucket/first/data.parquet"],
            "p.app.second": ["s3://bucket/second/data.parquet"],
        },
    )
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()

    with (
        patch("dp.loading.load_template", return_value="SELECT 1"),
        patch("dp.loading.bootstrap_table"),
        patch(
            "dp.loading.load_table",
            side_effect=[None, RuntimeError("boom")],
        ),
        patch("dp.loading.publish_prepared_tables") as publish,
    ):
        apply_sync_plan(pg_conn, duckdb, config, plan)

    publish.assert_called_once_with(pg_conn, [config.tables[0]])


def test_prepare_tables_incrementally_replaces_affected_partitions() -> None:
    """Existing partitioned tables retain old rows and load only changed paths."""
    table = PartitionedTable(name="p.app.people")
    changed = physical_partition("10")
    removed = physical_partition("20")
    path = "s3://bucket/app/people/partitions/10/data.parquet"
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": changed},
                changed_paths={"10": path},
                removed_partitions={"20": removed},
            )
        },
    )
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()

    with (
        patch("dp.loading.load_template", return_value="SELECT 1"),
        patch("dp.loading.create_incremental_shadow") as create_shadow,
        patch("dp.loading.bootstrap_table"),
        patch("dp.loading.load_table") as load,
    ):
        prepared = prepare_tables(
            pg_conn, duckdb, SyncConfig(tables=[table]), plan, {table.name}
        )

    create_shadow.assert_called_once_with(pg_conn, table, [changed, removed])
    load.assert_called_once_with(duckdb, "app", "people__next", [path])
    assert prepared == [table]


def test_prepare_tables_full_rebuilds_partitioned_table_from_parquet() -> None:
    """A full-rebuild partitioned table starts from Parquet, not a live copy."""
    table = PartitionedTable(name="p.app.people")
    partition = physical_partition("10")
    path = "s3://bucket/app/people/partitions/10/data.parquet"
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"10": partition},
                changed_paths={"10": path},
                removed_partitions={},
            )
        },
    )
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()
    rendered: list[TemplateSpec] = []

    def render(spec: TemplateSpec) -> str:
        rendered.append(spec)
        return "SELECT 1"

    with (
        patch("dp.loading.load_template", side_effect=render),
        patch("dp.loading.create_incremental_shadow") as create_shadow,
        patch("dp.loading.bootstrap_table"),
        patch("dp.loading.load_table") as load,
    ):
        prepared = prepare_tables(
            pg_conn, duckdb, SyncConfig(tables=[table]), plan, {table.name}
        )

    assert "duckdb/create_table_from_parquet" in [
        template_name(spec) for spec in rendered
    ]
    create_shadow.assert_not_called()
    load.assert_called_once_with(duckdb, "app", "people__next", [path])
    assert prepared == [table]


def test_prepare_tables_secures_shadow_before_load() -> None:
    """Grants and RLS run on the empty shadow before any data loads."""
    config = SyncConfig(
        tables=[FullTable(name="p.app.changed", rls=RlsConfig(column="unit_id"))]
    )
    path = "s3://bucket/changed/data.parquet"
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.app.changed": "100"},
        paths={"p.app.changed": [path]},
    )
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()
    calls: list[str] = []

    def record_bootstrap(*args: object) -> None:
        calls.append("bootstrap")

    def record_load(*args: object) -> None:
        calls.append("load")

    with (
        patch("dp.loading.load_template", return_value="SELECT 1"),
        patch("dp.loading.bootstrap_table", side_effect=record_bootstrap),
        patch("dp.loading.load_table", side_effect=record_load),
    ):
        prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

    assert calls == ["bootstrap", "load"]


def test_publish_prepared_tables_swaps_each_table() -> None:
    """Every prepared shadow table is atomically published."""
    connection = FakePgConn()
    tables: list[FullTable | PartitionedTable] = [
        FullTable(name="p.app.one"),
        FullTable(name="p.app.two"),
    ]

    with patch("dp.loading.publish_table") as publish:
        publish_prepared_tables(postgres_connection(connection), tables)

    assert publish.call_count == 2


def test_apply_sync_plan_delegates_all_steps() -> None:
    """The orchestrator receives connections and runs every step."""
    config = SyncConfig(tables=[FullTable(name="p.app.changed")])
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

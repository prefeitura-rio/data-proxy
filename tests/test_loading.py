"""Tests for Parquet-to-PostgreSQL loading operations."""

from datetime import UTC, datetime
from unittest.mock import ANY, call, patch

import pytest
from helpers import FakeDuckDBConnection, FakePgConn, postgres_connection
from psycopg.sql import Composable

from dp.authorization import (
    bootstrap_table,
    claim_session_var,
    schema_scope_predicate,
)
from dp.freshness import (
    delete_freshness,
    update_published_freshness,
    upsert_freshness,
)
from dp.loading import apply_sync_plan, validate_sync_plan
from dp.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    TimeRangeSelection,
    UnitMapping,
)
from dp.publication import (
    create_incremental_shadow,
    load_table,
    partition_predicate,
    prepare_tables,
    publish_prepared_tables,
    publish_table,
    reduce_sync_plan,
)
from dp.schema import initialize_schemas, reload_postgrest
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

    with patch("dp.publication.load_template", side_effect=render):
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
    """A non-RLS table receives its read grant and a schema-scope policy."""
    connection = FakePgConn()

    with patch("dp.authorization.load_template", side_effect=template_name):
        bootstrap_table(
            postgres_connection(connection),
            schema="app",
            table_name="table",
            rls=None,
            claim=None,
        )

    assert connection.executed == [b"pg/grant_select;pg/schema_scope_check"]


def test_bootstrap_installs_access_policy_check() -> None:
    """A protected table renders grants and its access_policy check together."""
    connection = FakePgConn()

    with patch("dp.authorization.load_template", side_effect=template_name):
        bootstrap_table(
            postgres_connection(connection),
            schema="app",
            table_name="table",
            rls=[UnitMapping(column="id_cras", unit_type="cras")],
            claim="preferred_username",
        )

    assert connection.executed == [b"pg/grant_select;pg/access_policy_check"]


def test_schema_scope_predicate_checks_the_mirrored_schemas_claim() -> None:
    """The schema-scope predicate matches one schema against the schemas claim."""
    rendered = schema_scope_predicate("app").as_string(None)

    assert "'app'" in rendered
    assert "'app.claim_schemas'" in rendered
    assert "string_to_array" in rendered


def test_bootstrap_requires_a_configured_claim_for_protected_tables() -> None:
    """A protected table without a configured schema claim fails loudly."""
    connection = FakePgConn()

    with (
        patch("dp.authorization.load_template", return_value="SELECT 1"),
        pytest.raises(RuntimeError, match="identity claim"),
    ):
        bootstrap_table(
            postgres_connection(connection),
            schema="app",
            table_name="table",
            rls=[UnitMapping(column="id_cras", unit_type="cras")],
            claim=None,
        )


def test_claim_session_var_uses_generic_naming() -> None:
    """Every claim maps to its generic `app.claim_<name>` session variable."""
    assert (
        claim_session_var("preferred_username").as_string(None)
        == "'app.claim_preferred_username'"
    )


def test_load_table_uses_exact_paths() -> None:
    """Only explicitly planned Parquet paths are loaded."""
    duckdb = FakeDuckDBConnection()
    paths = ["s3://bucket/table/a.parquet", "s3://bucket/table/b.parquet"]

    with patch("dp.publication.load_template", return_value="SELECT 1"):
        load_table(duckdb, "app", "table__next", paths)

    assert duckdb.executed == ["SELECT 1", "SELECT 1"]


def test_publish_table_swaps_before_index_creation() -> None:
    """A prepared table is bootstrapped, swapped, and indexed in order."""
    connection = FakePgConn()
    table = FullTable(
        name="p.app.table",
        indexes=[IndexConfig(name="idx_table", columns=["id"])],
    )

    with patch("dp.publication.load_template", side_effect=template_name):
        publish_table(postgres_connection(connection), table)

    assert connection.executed == [
        b"pg/swap_table",
        b"pg/create_index",
    ]


def test_initialize_schemas_creates_roles_then_schemas() -> None:
    """Roles, then each schema and its policy_writer role, are created and committed."""
    connection = FakePgConn()
    config = SyncConfig(
        schemas={
            "app": SchemaConfig(tables=[FullTable(name="p.app.one")]),
            "other": SchemaConfig(tables=[FullTable(name="p.other.two")]),
        }
    )

    with (
        patch("dp.schema.load_template", side_effect=template_name),
        patch("dp.authorization.load_template", side_effect=template_name),
    ):
        initialize_schemas(postgres_connection(connection), config)

    assert connection.executed[0] == b"pg/init_roles"
    assert connection.executed[1:] == [
        b"pg/init_schema",
        b"pg/access_policy_writer",
        b"pg/init_schema",
        b"pg/access_policy_writer",
    ]


def test_reload_postgrest_revokes_then_notifies() -> None:
    """Anonymous access is revoked per schema before the reload notification."""
    connection = FakePgConn()
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.one")])}
    )

    with patch("dp.schema.load_template", return_value="SELECT 1"):
        reload_postgrest(postgres_connection(connection), config)

    assert connection.executed == [
        b"SELECT 1",
        b"NOTIFY pgrst, 'reload schema'",
    ]


def test_prepare_tables_skips_table_with_missing_paths() -> None:
    """A changed table absent from the plan's paths is logged and skipped."""
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
    )
    plan = SyncPlan(sync_id="s1", signatures={}, paths={})
    pg_conn = postgres_connection(FakePgConn())
    duckdb = FakeDuckDBConnection()

    prepared = prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

    assert prepared == []


def test_validate_sync_plan_rejects_unknown_table() -> None:
    """A plan cannot publish a table absent from the mounted config."""
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.known")])}
    )
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
        schemas={
            "app": SchemaConfig(
                tables=[
                    FullTable(name="p.app.changed"),
                    FullTable(name="p.app.unchanged"),
                ]
            )
        }
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
        patch("dp.publication.load_template", return_value="SELECT 1"),
        patch("dp.publication.bootstrap_table") as bootstrap,
        patch("dp.publication.load_table") as load,
    ):
        prepared = prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

    bootstrap.assert_called_once_with(
        pg_conn,
        "app",
        "changed__next",
        None,
        None,
    )
    load.assert_called_once_with(ANY, "app", "changed__next", [path])
    assert [table.name for table in prepared] == ["p.app.changed"]


def test_apply_sync_plan_publishes_other_tables_after_a_load_failure() -> None:
    """A failed load skips only its own table, not the whole synchronization."""
    config = SyncConfig(
        schemas={
            "app": SchemaConfig(
                tables=[
                    FullTable(name="p.app.first"),
                    FullTable(name="p.app.second"),
                ]
            )
        }
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
        patch("dp.publication.load_template", return_value="SELECT 1"),
        patch("dp.publication.bootstrap_table"),
        patch(
            "dp.publication.load_table",
            side_effect=[None, RuntimeError("boom")],
        ),
        patch("dp.loading.publish_prepared_tables") as publish,
    ):
        apply_sync_plan(pg_conn, duckdb, config, plan)

    publish.assert_called_once_with(pg_conn, [config.tables[0]], plan, {}, ANY)


def test_reduce_sync_plan_keeps_plan_without_failures() -> None:
    """A plan without failed paths stays eligible without failure details."""
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            "p.app.people": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": physical_partition("10")},
                changed_paths={"10": "successful"},
                removed_partitions={},
            )
        },
    )

    decision = reduce_sync_plan(plan, set())
    reduced = decision.plan
    blocked = decision.blocked_tables
    failures = decision.failed_partitions

    assert reduced == plan
    assert blocked == set()
    assert failures == {}


def test_reduce_incremental_plan_keeps_failed_existing_partition() -> None:
    """A failed existing partition keeps its old manifest entry and path."""
    previous = physical_partition("10")
    current = previous.model_copy(update={"signature": "new"})
    successful = physical_partition("20")
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            "p.app.people": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": current, "20": successful},
                changed_paths={"10": "failed", "20": "successful"},
                previous_partitions={"10": previous},
                removed_partitions={},
            )
        },
    )

    decision = reduce_sync_plan(plan, {"failed"})
    reduced = decision.plan
    blocked = decision.blocked_tables
    failures = decision.failed_partitions
    table_plan = reduced.partitioned_tables["p.app.people"]

    assert blocked == set()
    assert failures == {"p.app.people": {"10"}}
    assert table_plan.changed_paths == {"20": "successful"}
    assert table_plan.current_partitions == {"10": previous, "20": successful}


def test_reduce_incremental_plan_omits_failed_new_partition() -> None:
    """A failed new partition is absent from the publication manifest."""
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            "p.app.people": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": physical_partition("10")},
                changed_paths={"10": "failed"},
                removed_partitions={},
            )
        },
    )

    decision = reduce_sync_plan(plan, {"failed"})
    reduced = decision.plan
    blocked = decision.blocked_tables

    assert blocked == set()
    assert reduced.partitioned_tables["p.app.people"].current_partitions == {}


def test_reduce_sync_plan_blocks_failed_full_rebuild() -> None:
    """A failed partition blocks a complete partitioned rebuild."""
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            "p.app.people": PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"10": physical_partition("10")},
                changed_paths={"10": "failed"},
                removed_partitions={},
            )
        },
    )

    decision = reduce_sync_plan(plan, {"failed"})

    assert decision.blocked_tables == {"p.app.people"}


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
        patch("dp.publication.load_template", return_value="SELECT 1"),
        patch("dp.publication.create_incremental_shadow") as create_shadow,
        patch("dp.publication.bootstrap_table"),
        patch("dp.publication.load_table") as load,
    ):
        prepared = prepare_tables(
            pg_conn,
            duckdb,
            SyncConfig(schemas={"app": SchemaConfig(tables=[table])}),
            plan,
            {table.name},
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
        patch("dp.publication.load_template", side_effect=render),
        patch("dp.publication.create_incremental_shadow") as create_shadow,
        patch("dp.publication.bootstrap_table"),
        patch("dp.publication.load_table") as load,
    ):
        prepared = prepare_tables(
            pg_conn,
            duckdb,
            SyncConfig(schemas={"app": SchemaConfig(tables=[table])}),
            plan,
            {table.name},
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
        schemas={
            "app": SchemaConfig(
                claim="preferred_username",
                tables=[
                    FullTable(
                        name="p.app.changed",
                        rls=[UnitMapping(column="id_cras", unit_type="cras")],
                    )
                ],
            )
        }
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
        patch("dp.publication.load_template", return_value="SELECT 1"),
        patch("dp.publication.bootstrap_table", side_effect=record_bootstrap),
        patch("dp.publication.load_table", side_effect=record_load),
    ):
        prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

    assert calls == ["bootstrap", "load"]


def test_delete_freshness_uses_partition_template() -> None:
    """A partition removal uses its freshness SQL template."""
    connection = FakePgConn()
    table = PartitionedTable(name="p.app.people", resolved_schema="app")

    with patch("dp.freshness.load_template", return_value="DELETE") as render:
        delete_freshness(postgres_connection(connection), table, "10")

    assert render.call_args.args[0]["path"] == "pg/delete_partition_freshness"
    assert connection.executed == [b"DELETE"]


def test_upsert_freshness_uses_shared_status_enum_template() -> None:
    """A freshness write uses the shared enum SQL template and values."""
    connection = FakePgConn()
    table = PartitionedTable(name="p.app.people", resolved_schema="app")
    attempted_at = datetime.now(UTC)

    with patch("dp.freshness.load_template", return_value="UPSERT") as render:
        upsert_freshness(
            postgres_connection(connection),
            table,
            "10",
            attempted_at,
            success=False,
        )

    assert render.call_args.args[0]["path"] == "pg/upsert_freshness"
    assert connection.executed == [b"UPSERT"]


def test_full_rebuild_freshness_replaces_all_partition_rows() -> None:
    """A full rebuild resets freshness to its complete current manifest."""
    table = PartitionedTable(name="p.app.people")
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"10": physical_partition("10")},
                changed_paths={"10": "successful"},
                removed_partitions={},
            )
        },
    )
    connection = postgres_connection(FakePgConn())
    attempted_at = datetime.now(UTC)

    with (
        patch("dp.freshness.load_template", return_value="DELETE"),
        patch("dp.freshness.upsert_freshness") as upsert,
    ):
        update_published_freshness(connection, table, plan, set(), attempted_at)

    upsert.assert_called_once_with(connection, table, "10", attempted_at, success=True)


def test_incremental_freshness_records_success_failure_and_removal() -> None:
    """Freshness matches each result in a partial partition publication."""
    table = PartitionedTable(name="p.app.people")
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": physical_partition("10")},
                changed_paths={"10": "successful"},
                removed_partitions={"30": physical_partition("30")},
            )
        },
    )
    connection = postgres_connection(FakePgConn())
    attempted_at = datetime.now(UTC)

    with (
        patch("dp.freshness.upsert_freshness") as upsert,
        patch("dp.freshness.delete_freshness") as delete,
    ):
        update_published_freshness(connection, table, plan, {"20"}, attempted_at)

    assert upsert.call_args_list == [
        call(connection, table, "10", attempted_at, success=True),
        call(connection, table, "20", attempted_at, success=False),
    ]
    delete.assert_called_once_with(connection, table, "30")


def test_publish_prepared_tables_swaps_each_table() -> None:
    """Every prepared shadow table is atomically published."""
    connection = FakePgConn()
    tables: list[FullTable | PartitionedTable] = [
        FullTable(name="p.app.one"),
        FullTable(name="p.app.two"),
    ]

    plan = SyncPlan(
        sync_id="s1",
        signatures={table.name: "new" for table in tables},
        paths={table.name: [f"s3://b/{table.table_name}"] for table in tables},
    )
    with patch("dp.publication.publish_table") as publish:
        result = publish_prepared_tables(
            postgres_connection(connection),
            tables,
            plan,
            {},
            datetime.now(UTC),
        )

    assert publish.call_count == 2
    assert result == {"p.app.one", "p.app.two"}


def test_publish_prepared_tables_excludes_failed_publication() -> None:
    """A failed swap is not reported as synchronized."""
    connection = FakePgConn()
    tables = [FullTable(name="p.app.one"), FullTable(name="p.app.two")]

    plan = SyncPlan(
        sync_id="s1",
        signatures={table.name: "new" for table in tables},
        paths={table.name: [f"s3://b/{table.table_name}"] for table in tables},
    )
    with patch(
        "dp.publication.publish_table", side_effect=[RuntimeError("boom"), None]
    ):
        result = publish_prepared_tables(
            postgres_connection(connection),
            tables,
            plan,
            {"p.app.one": {"10"}},
            datetime.now(UTC),
        )

    assert result == {"p.app.two"}


def test_apply_sync_plan_delegates_all_steps() -> None:
    """The orchestrator receives connections and runs every step."""
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
    )
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
        patch(
            "dp.loading.publish_prepared_tables",
            return_value={"p.app.changed"},
        ) as publish,
        patch("dp.loading.reload_postgrest") as reload,
    ):
        result = apply_sync_plan(pg_conn, duckdb, config, plan)

    initialize.assert_called_once()
    publish.assert_called_once()
    reload.assert_called_once()
    assert result.plan == plan
    assert result.published_tables == {"p.app.changed"}


def test_apply_sync_plan_records_failure_without_incremental_publication() -> None:
    """A fully failed incremental change records failure without a swap."""
    table = PartitionedTable(name="p.app.people")
    path = "s3://bucket/people/10.parquet"
    plan = SyncPlan(
        sync_id="s1",
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": physical_partition("10")},
                changed_paths={"10": path},
                removed_partitions={},
            )
        },
    )
    pg_conn = postgres_connection(FakePgConn())

    with (
        patch("dp.loading.initialize_schemas"),
        patch("dp.loading.prepare_tables", return_value=[]) as prepare,
        patch("dp.loading.upsert_freshness") as upsert,
        patch("dp.loading.publish_prepared_tables", return_value=set()),
        patch("dp.loading.reload_postgrest"),
    ):
        result = apply_sync_plan(
            pg_conn,
            FakeDuckDBConnection(),
            SyncConfig(schemas={"app": SchemaConfig(tables=[table])}),
            plan,
            {path},
        )

    prepare.assert_called_once_with(pg_conn, ANY, ANY, ANY, set())
    upsert.assert_called_once_with(pg_conn, table, "10", ANY, success=False)
    assert result.published_tables == set()


def test_apply_sync_plan_excludes_extraction_failures() -> None:
    """A table with a failed extraction is not prepared from stale Parquet."""
    config = SyncConfig(
        schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
    )
    plan = SyncPlan(
        sync_id="s1",
        signatures={"p.app.changed": "100"},
        paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
    )
    pg_conn = postgres_connection(FakePgConn())

    with (
        patch("dp.loading.initialize_schemas"),
        patch("dp.loading.prepare_tables", return_value=[]) as prepare,
        patch("dp.loading.publish_prepared_tables", return_value=set()),
        patch("dp.loading.reload_postgrest"),
    ):
        result = apply_sync_plan(
            pg_conn,
            FakeDuckDBConnection(),
            config,
            plan,
            {"s3://bucket/changed/data.parquet"},
        )

    prepare.assert_called_once_with(pg_conn, ANY, config, plan, set())
    assert result.plan == plan
    assert result.published_tables == set()

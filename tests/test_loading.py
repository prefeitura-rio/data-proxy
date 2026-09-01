"""Tests for Parquet-to-PostgreSQL loading operations."""

from pathlib import Path
from typing import LiteralString, cast
from unittest.mock import ANY, call, patch

import pytest
from duckdb import connect
from psycopg import Connection
from psycopg.sql import SQL, Composable
from whenever import Instant

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
from dp.loading import apply_sync_plan
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
from dp.templates import TemplateSpec, load_template
from tests.helpers import partition


class TestLoadingPartitionPredicate:
    """Tests for PartitionPredicate behavior."""

    def test_partition_predicate_matches_remainder_rows_outside_the_range(
        self,
    ) -> None:
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

    def test_partition_predicate_matches_bounded_range_rows(
        self,
    ) -> None:
        """An ordinary partition's predicate matches its [lower, upper) bounds."""
        bounded = PhysicalPartition(
            partition_id="10",
            signature="signature",
            selection=RangeSelection(
                partition_id="10", column="cpf", lower=10, upper=20
            ),
        )

        rendered = partition_predicate(bounded).as_string(None)

        assert '"cpf" >= 10' in rendered
        assert '"cpf" < 20' in rendered

    def test_partition_predicate_matches_time_range_rows(
        self,
    ) -> None:
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


class TestLoadingPrepareTablesPaths:
    """Tests for planned table paths."""

    def test_prepare_tables_skips_table_with_missing_paths(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A changed table absent from the plan's paths is logged and skipped."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
        )
        plan = SyncPlan(schema_name="app")
        duckdb = connect(":memory:")

        with patch("dp.publication.load_template", return_value="SELECT 1"):
            prepared = prepare_tables(postgres, duckdb, config, plan, {"p.app.changed"})

        assert prepared == []

    def test_prepare_tables_uses_exact_planned_paths(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
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
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": [path]},
        )
        duckdb = connect(":memory:")

        with (
            patch("dp.publication.load_template", return_value="SELECT 1"),
            patch("dp.publication.bootstrap_table") as bootstrap,
            patch("dp.publication.load_table") as load,
        ):
            prepared = prepare_tables(postgres, duckdb, config, plan, {"p.app.changed"})

        bootstrap.assert_called_once_with(
            postgres,
            "app",
            "changed__next",
            None,
            None,
        )
        load.assert_called_once_with(ANY, "app", "changed__next", [path])
        assert [table.name for table in prepared] == ["p.app.changed"]


class TestLoadingReduceIncremental:
    """Tests for ReduceIncremental behavior."""

    def test_reduce_incremental_plan_keeps_failed_existing_partition(
        self,
    ) -> None:
        """A failed existing partition keeps its old manifest entry and path."""
        previous = partition("10")
        current = previous.model_copy(update={"signature": "new"})
        successful = partition("20")
        plan = SyncPlan(
            schema_name="app",
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

    def test_reduce_incremental_plan_omits_failed_new_partition(
        self,
    ) -> None:
        """A failed new partition is absent from the publication manifest."""
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
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


class TestLoadingPrepareTablesPartitions:
    """Tests for partitioned table preparation."""

    def test_prepare_tables_incrementally_replaces_affected_partitions(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Existing partitioned tables retain old rows and load only changed paths."""
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        changed = partition("10")
        removed = partition("20")
        path = "s3://bucket/app/people/partitions/10/data.parquet"
        plan = SyncPlan(
            schema_name="app",
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
        pg_conn = postgres
        duckdb = connect(":memory:")

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

    def test_prepare_tables_full_rebuilds_partitioned_from_parquet(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A full-rebuild partitioned table starts from Parquet, not a live copy."""
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        current_partition = partition("10")
        path = "s3://bucket/app/people/partitions/10/data.parquet"
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": current_partition},
                    changed_paths={"10": path},
                    removed_partitions={},
                )
            },
        )
        pg_conn = postgres
        duckdb = connect(":memory:")
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

        assert "duckdb/create_table_from_parquet" in [spec.path for spec in rendered]
        create_shadow.assert_not_called()
        load.assert_called_once_with(duckdb, "app", "people__next", [path])
        assert prepared == [table]

    def test_prepare_tables_secures_shadow_before_load(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
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
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": [path]},
        )
        pg_conn = postgres
        duckdb = connect(":memory:")
        calls: list[str] = []

        def record_bootstrap(*_: object) -> None:
            calls.append("bootstrap")

        def record_load(*_: object) -> None:
            calls.append("load")

        with (
            patch("dp.publication.load_template", return_value="SELECT 1"),
            patch("dp.publication.bootstrap_table", side_effect=record_bootstrap),
            patch("dp.publication.load_table", side_effect=record_load),
        ):
            prepare_tables(pg_conn, duckdb, config, plan, {"p.app.changed"})

        assert calls == ["bootstrap", "load"]


class TestLoadingPublishPrepared:
    """Tests for PublishPrepared behavior."""

    def test_publish_prepared_tables_swaps_each_table(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Every prepared shadow table is atomically published."""
        connection = postgres
        tables: list[FullTable | PartitionedTable] = [
            FullTable(name="p.app.one", resolved_schema="app"),
            FullTable(name="p.app.two", resolved_schema="app"),
        ]

        plan = SyncPlan(
            schema_name="app",
            signatures={table.name: "new" for table in tables},
            paths={table.name: [f"s3://b/{table.table_name}"] for table in tables},
        )
        with patch("dp.publication.publish_table") as publish:
            result = publish_prepared_tables(
                (connection),
                tables,
                plan,
                {},
                Instant.now(),
            )

        assert publish.call_count == 2
        assert result == {"p.app.one", "p.app.two"}

    def test_publish_prepared_tables_excludes_failed_publication(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A failed swap is not reported as synchronized."""
        connection = postgres
        tables = [
            FullTable(name="p.app.one", resolved_schema="app"),
            FullTable(name="p.app.two", resolved_schema="app"),
        ]

        plan = SyncPlan(
            schema_name="app",
            signatures={table.name: "new" for table in tables},
            paths={table.name: [f"s3://b/{table.table_name}"] for table in tables},
        )
        with patch(
            "dp.publication.publish_table", side_effect=[RuntimeError("boom"), None]
        ):
            result = publish_prepared_tables(
                (connection),
                tables,
                plan,
                {"p.app.one": {"10"}},
                Instant.now(),
            )

        assert result == {"p.app.two"}


class TestLoadingApplySyncPlan:
    """Tests for ApplySyncPlan behavior."""

    def test_apply_sync_plan_delegates_all_steps(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """The orchestrator receives connections and runs every step."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
        )
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )
        pg_conn = postgres
        duckdb = connect(":memory:")

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

    def test_apply_sync_plan_records_failure_without_incremental_publication(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A fully failed incremental change records failure without a swap."""
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        path = "s3://bucket/people/10.parquet"
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": path},
                    removed_partitions={},
                )
            },
        )
        pg_conn = postgres

        with (
            patch("dp.loading.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.record_table_failures") as record_failures,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
        ):
            result = apply_sync_plan(
                pg_conn,
                connect(":memory:"),
                SyncConfig(schemas={"app": SchemaConfig(tables=[table])}),
                plan,
                {path},
            )

        prepare.assert_called_once_with(pg_conn, ANY, ANY, ANY, set())
        record_failures.assert_called_once_with(
            pg_conn, [table], plan, ANY, {table.name: {"10"}}
        )
        assert result.published_tables == set()

    def test_apply_sync_plan_excludes_extraction_failures(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A table with a failed extraction is not prepared from stale Parquet."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
        )
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )
        pg_conn = postgres

        with (
            patch("dp.loading.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
        ):
            result = apply_sync_plan(
                pg_conn,
                connect(":memory:"),
                config,
                plan,
                {"s3://bucket/changed/data.parquet"},
            )

        prepare.assert_called_once_with(pg_conn, ANY, config, plan, set())
        assert result.plan == plan
        assert result.published_tables == set()


class TestLoading:
    """Tests for loading module behavior."""

    def test_create_incremental_shadow_excludes_affected_ranges(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Shadow preparation copies only rows outside changed physical bounds."""
        rendered: list[TemplateSpec] = []

        def render(spec: TemplateSpec) -> str:
            rendered.append(spec)
            return "SELECT 1"

        with patch("dp.publication.load_template", side_effect=render):
            create_incremental_shadow(
                postgres,
                PartitionedTable(name="p.app.people"),
                [partition("10"), partition("20")],
            )

        assert [spec.path for spec in rendered] == [
            "pg/partition_range_predicate",
            "pg/partition_range_predicate",
            "pg/prepare_incremental_table",
        ]
        predicate = rendered[-1].mapping["affected_partitions"]
        assert isinstance(predicate, Composable)

    def test_bootstrap_grants_access_without_rls(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A non-RLS table receives its read grant and a schema-scope policy."""
        postgres.execute(
            SQL(
                cast(
                    LiteralString,
                    load_template(
                        TemplateSpec(
                            path="postgres/create_table",
                            mapping={
                                "schema": "app",
                                "table": "table",
                                "columns": "id_cras text",
                            },
                        ),
                        Path(__file__).parent / "sql",
                    ),
                )
            )
        )

        bootstrap_table(
            postgres,
            schema="app",
            table_name="table",
            rls=None,
            claim=None,
        )

        relrowsecurity = postgres.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'app.table'::regclass"
        ).fetchone()
        assert relrowsecurity == (True,)
        policies = postgres.execute(
            "".join(
                (
                    "SELECT policyname FROM pg_policies ",
                    "WHERE schemaname = 'app' AND tablename = 'table'",
                )
            )
        ).fetchall()
        assert policies == [("schema_scoped",)]
        grantee = postgres.execute(
            "".join(
                (
                    "SELECT grantee FROM information_schema.role_table_grants ",
                    "WHERE table_schema = 'app' AND table_name = 'table' ",
                    "AND privilege_type = 'SELECT' AND grantee = 'user'",
                )
            )
        ).fetchall()
        assert grantee == [("user",)]

    def test_bootstrap_installs_access_policy_check(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A protected table renders grants and its access_policy check together."""
        postgres.execute(
            SQL(
                cast(
                    LiteralString,
                    load_template(
                        TemplateSpec(
                            path="postgres/create_table",
                            mapping={
                                "schema": "app",
                                "table": "table",
                                "columns": "id_cras text",
                            },
                        ),
                        Path(__file__).parent / "sql",
                    ),
                )
            )
        )
        postgres.execute(
            "".join(
                (
                    "CREATE TABLE app.access_policy (",
                    " subject text, is_enabled boolean, is_admin boolean, ",
                    "unit_type text, id_cras text, unit_id text)",
                )
            )
        )

        bootstrap_table(
            postgres,
            schema="app",
            table_name="table",
            rls=[UnitMapping(column="id_cras", unit_type="cras")],
            claim="preferred_username",
        )

        policies = postgres.execute(
            "".join(
                (
                    "SELECT policyname FROM pg_policies ",
                    "WHERE schemaname = 'app' AND tablename = 'table'",
                )
            )
        ).fetchall()
        assert policies == [("access_policy_scoped",)]

    def test_schema_scope_predicate_checks_the_mirrored_schemas_claim(
        self,
    ) -> None:
        """The schema-scope predicate matches one schema against the schemas claim."""
        rendered = schema_scope_predicate("app").as_string(None)

        assert "'app'" in rendered
        assert "'app.claim_schemas'" in rendered
        assert "string_to_array" in rendered

    def test_bootstrap_requires_a_configured_claim_for_protected_tables(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A protected table without a configured schema claim fails loudly."""
        with pytest.raises(RuntimeError, match="identity claim"):
            bootstrap_table(
                postgres,
                schema="app",
                table_name="table",
                rls=[UnitMapping(column="id_cras", unit_type="cras")],
                claim=None,
            )

    def test_claim_session_var_uses_generic_naming(
        self,
    ) -> None:
        """Every claim maps to its generic `app.claim_<name>` session variable."""
        assert (
            claim_session_var("preferred_username").as_string(None)
            == "'app.claim_preferred_username'"
        )

    def test_load_table_uses_exact_paths(
        self,
    ) -> None:
        """Only explicitly planned Parquet paths are loaded."""
        duckdb = connect(":memory:")
        paths = ["s3://bucket/table/a.parquet", "s3://bucket/table/b.parquet"]

        with patch("dp.publication.load_template", return_value="SELECT 1"):
            load_table(duckdb, "app", "table__next", paths)

        assert duckdb.execute("SELECT 1").fetchone() == (1,)

    def test_publish_table_swaps_before_index_creation(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A prepared table is bootstrapped, swapped, and indexed in order."""
        postgres.execute(
            SQL(
                cast(
                    LiteralString,
                    load_template(
                        TemplateSpec(
                            path="postgres/create_table",
                            mapping={
                                "schema": "app",
                                "table": "table__next",
                                "columns": "id int",
                            },
                        ),
                        Path(__file__).parent / "sql",
                    ),
                )
            )
        )
        table = FullTable(
            name="p.app.table",
            resolved_schema="app",
            indexes=[IndexConfig(name="idx_table", columns=["id"])],
        )

        publish_table(postgres, table)

        relation = postgres.execute(
            "SELECT to_regclass('app.table'), to_regclass('app.table__next')"
        ).fetchone()
        assert relation == ('app."table"', None)
        indexes = postgres.execute(
            "".join(
                (
                    "SELECT indexname FROM pg_indexes ",
                    "WHERE schemaname = 'app' AND tablename = 'table'",
                )
            )
        ).fetchall()
        assert indexes == [("idx_table",)]

    def test_initialize_schemas_creates_roles_then_schemas(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Roles, schemas, local policies, and policy writers are created in order."""
        connection = postgres
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(tables=[FullTable(name="p.app.one")]),
                "other": SchemaConfig(tables=[FullTable(name="p.other.two")]),
            }
        )

        initialize_schemas(connection, config)

        schemas = postgres.execute(
            "".join(
                (
                    "SELECT schema_name FROM information_schema.schemata ",
                    "WHERE schema_name IN ('app', 'other') ORDER BY schema_name",
                )
            )
        ).fetchall()
        assert schemas == [("app",), ("other",)]

    def test_reload_postgrest_revokes_then_notifies(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Anonymous access is revoked per schema before the reload notification."""
        connection = postgres
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.one")])}
        )

        reload_postgrest(connection, config)

        assert postgres.execute("SELECT 1").fetchone() == (1,)

    def test_apply_sync_plan_publishes_other_tables_after_a_load_failure(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
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
            schema_name="app",
            signatures={
                "p.app.first": "1",
                "p.app.second": "2",
            },
            paths={
                "p.app.first": ["s3://bucket/first/data.parquet"],
                "p.app.second": ["s3://bucket/second/data.parquet"],
            },
        )
        pg_conn = postgres
        duckdb = connect(":memory:")

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

    def test_reduce_sync_plan_keeps_plan_without_failures(
        self,
    ) -> None:
        """A plan without failed paths stays eligible without failure details."""
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
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

    def test_reduce_sync_plan_blocks_failed_full_rebuild(
        self,
    ) -> None:
        """A failed partition blocks a complete partitioned rebuild."""
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "failed"},
                    removed_partitions={},
                )
            },
        )

        decision = reduce_sync_plan(plan, {"failed"})

        assert decision.blocked_tables == {"p.app.people"}

    def test_delete_freshness_uses_partition_template(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A partition removal uses its freshness SQL template."""
        connection = postgres
        table = PartitionedTable(name="p.app.people", resolved_schema="app")

        delete_freshness(connection, table, {"10"})

        assert postgres.execute("SELECT count(*) FROM app.freshness").fetchone() == (0,)

    def test_upsert_freshness_uses_shared_status_enum_template(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A freshness write uses the shared enum SQL template and values."""
        connection = postgres
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        attempted_at = Instant.now()

        upsert_freshness(connection, table, {"10"}, attempted_at, success=False)

        assert postgres.execute(
            "SELECT status::text FROM app.freshness"
        ).fetchone() == ("failure",)

    def test_full_rebuild_freshness_replaces_all_partition_rows(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """A full rebuild resets freshness to its complete current manifest."""
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "successful"},
                    removed_partitions={},
                )
            },
        )
        connection = postgres
        attempted_at = Instant.now()

        update_published_freshness(connection, table, plan, set(), attempted_at)

        assert postgres.execute(
            "SELECT partition, status::text FROM app.freshness"
        ).fetchall() == [("10", "success")]

    def test_incremental_freshness_records_success_failure_and_removal(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Freshness matches each result in a partial partition publication."""
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "successful"},
                    removed_partitions={"30": partition("30")},
                )
            },
        )
        connection = postgres
        attempted_at = Instant.now()

        with (
            patch("dp.freshness.upsert_freshness") as upsert,
            patch("dp.freshness.delete_freshness") as delete,
        ):
            update_published_freshness(connection, table, plan, {"20"}, attempted_at)

        assert upsert.call_args_list == [
            call(connection, table, {"10"}, attempted_at, success=True),
            call(connection, table, {"20"}, attempted_at, success=False),
        ]
        delete.assert_called_once_with(connection, table, {"30"})

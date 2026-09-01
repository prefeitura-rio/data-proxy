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
from tests.constants import FILES
from tests.helpers import partition


class TestLoadingPartitionPredicate:
    """Tests for PartitionPredicate behavior."""

    def test_partition_predicate_matches_remainder_rows_outside_the_range(
        self,
    ) -> None:
        """
        GIVEN: a remainder partition with [0, 100) bounds.
        WHEN: partition_predicate is rendered.
        THEN: it matches null and out-of-range rows.
        """
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
        """
        GIVEN: a bounded range partition with [10, 20) bounds.
        WHEN: partition_predicate is rendered.
        THEN: it matches rows within the [lower, upper) range.
        """
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
        """
        GIVEN: a time range partition with [2025-01-01, 2025-01-02) bounds.
        WHEN: partition_predicate is rendered.
        THEN: it matches rows within the [lower, upper) date range.
        """
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
        """
        GIVEN: a changed table with no entry in the plan paths.
        WHEN: prepare_tables runs.
        THEN: it returns no prepared tables.
        """
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
        """
        GIVEN: a plan with exact paths for one changed table.
        WHEN: prepare_tables runs.
        THEN: it loads only the planned table and its exact paths.
        """
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
        """
        GIVEN: a failed existing partition with a previous manifest entry.
        WHEN: reduce_sync_plan is called.
        THEN: the old manifest entry and path are kept and the partition is recorded as failed.
        """
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
        """
        GIVEN: a failed new partition without a previous manifest entry.
        WHEN: reduce_sync_plan is called.
        THEN: the partition is absent from the publication manifest.
        """
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
        """
        GIVEN: an existing partitioned table with changed and removed partitions.
        WHEN: prepare_tables runs incrementally.
        THEN: old rows are retained and only changed paths are loaded.
        """
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
        duckdb = connect(":memory:")

        with (
            patch("dp.publication.load_template", return_value="SELECT 1"),
            patch("dp.publication.create_incremental_shadow") as create_shadow,
            patch("dp.publication.bootstrap_table"),
            patch("dp.publication.load_table") as load,
        ):
            prepared = prepare_tables(
                postgres,
                duckdb,
                SyncConfig(schemas={"app": SchemaConfig(tables=[table])}),
                plan,
                {table.name},
            )

        create_shadow.assert_called_once_with(postgres, table, [changed, removed])
        load.assert_called_once_with(duckdb, "app", "people__next", [path])
        assert prepared == [table]

    def test_prepare_tables_full_rebuilds_partitioned_from_parquet(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a full-rebuild partitioned table.
        WHEN: prepare_tables runs.
        THEN: the table starts from Parquet instead of a live copy.
        """
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
                postgres,
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
        """
        GIVEN: a table with RLS configuration.
        WHEN: prepare_tables runs.
        THEN: grants and RLS run on the empty shadow before any data loads.
        """
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
            prepare_tables(postgres, duckdb, config, plan, {"p.app.changed"})

        assert calls == ["bootstrap", "load"]


class TestLoadingPublishPrepared:
    """Tests for PublishPrepared behavior."""

    def test_publish_prepared_tables_swaps_each_table(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: multiple prepared shadow tables.
        WHEN: publish_prepared_tables runs.
        THEN: each table is atomically published.
        """
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
                (postgres),
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
        """
        GIVEN: one table swap raises RuntimeError.
        WHEN: publish_prepared_tables runs.
        THEN: only the successful tables are reported as synchronized.
        """
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
                (postgres),
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
        """
        GIVEN: a sync config and plan with changes.
        WHEN: apply_sync_plan runs.
        THEN: the orchestrator delegates to initialize, prepare, publish, and reload.
        """
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
        )
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )
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
            result = apply_sync_plan(postgres, duckdb, config, plan)

        initialize.assert_called_once()
        publish.assert_called_once()
        reload.assert_called_once()
        assert result.plan == plan
        assert result.published_tables == {"p.app.changed"}

    def test_apply_sync_plan_records_failure_without_incremental_publication(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a fully failed incremental change.
        WHEN: apply_sync_plan runs.
        THEN: it records failure without performing a publication swap.
        """
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

        with (
            patch("dp.loading.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.record_table_failures") as record_failures,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
        ):
            result = apply_sync_plan(
                postgres,
                connect(":memory:"),
                SyncConfig(schemas={"app": SchemaConfig(tables=[table])}),
                plan,
                {path},
            )

        prepare.assert_called_once_with(postgres, ANY, ANY, ANY, set())
        record_failures.assert_called_once_with(
            postgres, [table], plan, ANY, {table.name: {"10"}}
        )
        assert result.published_tables == set()

    def test_apply_sync_plan_excludes_extraction_failures(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a table with a failed extraction path.
        WHEN: apply_sync_plan runs.
        THEN: the table is not prepared from stale Parquet.
        """
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.changed")])}
        )
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )

        with (
            patch("dp.loading.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
        ):
            result = apply_sync_plan(
                postgres,
                connect(":memory:"),
                config,
                plan,
                {"s3://bucket/changed/data.parquet"},
            )

        prepare.assert_called_once_with(postgres, ANY, config, plan, set())
        assert result.plan == plan
        assert result.published_tables == set()


class TestLoading:
    """Tests for loading module behavior."""

    def test_create_incremental_shadow_excludes_affected_ranges(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a partitioned table with changed physical bounds.
        WHEN: create_incremental_shadow runs.
        THEN: it copies only rows outside the changed bounds.
        """
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
        """
        GIVEN: a non-RLS table.
        WHEN: bootstrap_table is called.
        THEN: it receives a read grant and a schema-scope policy.
        """
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
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/relrowsecurity", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone()
        assert relrowsecurity == (True,)
        policies = postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/policy_names", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall()
        assert policies == [("schema_scoped",)]
        grantee = postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/select_grants", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall()
        assert grantee == [("user",)]

    def test_bootstrap_installs_access_policy_check(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a protected table with RLS and an access_policy table.
        WHEN: bootstrap_table is called.
        THEN: it renders grants and the access_policy check together.
        """
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
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/create_access_policy", mapping={}),
                    FILES.parent / "sql",
                ),
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
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/policy_names", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall()
        assert policies == [("access_policy_scoped",)]

    def test_schema_scope_predicate_checks_the_mirrored_schemas_claim(
        self,
    ) -> None:
        """
        GIVEN: a schema name.
        WHEN: schema_scope_predicate is rendered.
        THEN: it checks the schema against the mirrored schemas claim.
        """
        rendered = schema_scope_predicate("app").as_string(None)

        assert "'app'" in rendered
        assert "'app.claim_schemas'" in rendered
        assert "string_to_array" in rendered

    def test_bootstrap_requires_a_configured_claim_for_protected_tables(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a protected table without a configured schema claim.
        WHEN: bootstrap_table is called.
        THEN: it raises RuntimeError.
        """
        with pytest.raises(RuntimeError, match="identity claim"):
            bootstrap_table(
                postgres,
                schema="app",
                table_name="table",
                rls=[UnitMapping(column="id_cras", unit_type="cras")],
                claim=None,
            )

    def test_claim_session_var_maps_to_generic_session_variable_name(
        self,
    ) -> None:
        """
        GIVEN: a claim name.
        WHEN: claim_session_var is rendered.
        THEN: it maps to the generic `app.claim_<name>` session variable.
        """
        assert (
            claim_session_var("preferred_username").as_string(None)
            == "'app.claim_preferred_username'"
        )

    def test_load_table_loads_only_explicitly_planned_paths(
        self,
    ) -> None:
        """
        GIVEN: explicitly planned Parquet paths.
        WHEN: load_table is called.
        THEN: only those paths are loaded.
        """
        duckdb = connect(":memory:")
        paths = ["s3://bucket/table/a.parquet", "s3://bucket/table/b.parquet"]

        with patch("dp.publication.load_template", return_value="SELECT 1"):
            load_table(duckdb, "app", "table__next", paths)

        assert duckdb.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/select_one", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone() == (1,)

    def test_publish_table_swaps_before_index_creation(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a prepared shadow table with an index configuration.
        WHEN: publish_table is called.
        THEN: the table is swapped before the index is created.
        """
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
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/regclass_table_and_shadow", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone()
        assert relation == ('app."table"', None)
        indexes = postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/index_names", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall()
        assert indexes == [("idx_table",)]

    def test_initialize_schemas_creates_roles_schemas_and_policies_in_order(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a sync config with multiple schemas.
        WHEN: initialize_schemas is called.
        THEN: roles, schemas, local policies, and policy writers are created in order.
        """
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(tables=[FullTable(name="p.app.one")]),
                "other": SchemaConfig(tables=[FullTable(name="p.other.two")]),
            }
        )

        initialize_schemas(postgres, config)

        schemas = postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/schema_names", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall()
        assert schemas == [("app",), ("other",)]

    def test_reload_postgrest_revokes_anonymous_then_notifies(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a sync config.
        WHEN: reload_postgrest is called.
        THEN: anonymous access is revoked per schema before the reload notification.
        """
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.one")])}
        )

        reload_postgrest(postgres, config)

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/select_one", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone() == (1,)

    def test_apply_sync_plan_publishes_other_tables_after_a_load_failure(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: two tables where the second load fails.
        WHEN: apply_sync_plan runs.
        THEN: only the failed table is skipped, not the whole synchronization.
        """
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
            apply_sync_plan(postgres, duckdb, config, plan)

        publish.assert_called_once_with(postgres, [config.tables[0]], plan, {}, ANY)

    def test_reduce_sync_plan_keeps_plan_without_failures(
        self,
    ) -> None:
        """
        GIVEN: a plan without failed paths.
        WHEN: reduce_sync_plan is called.
        THEN: the plan stays eligible with no failure details.
        """
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
        """
        GIVEN: a full rebuild plan with a failed partition.
        WHEN: reduce_sync_plan is called.
        THEN: the table is blocked from publication.
        """
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

    def test_delete_freshness_removes_specified_partitions(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a partitioned table with a partition to remove.
        WHEN: delete_freshness is called.
        THEN: the partition is removed using the freshness SQL template.
        """
        table = PartitionedTable(name="p.app.people", resolved_schema="app")

        delete_freshness(postgres, table, {"10"})

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/freshness_count", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone() == (0,)

    def test_upsert_freshness_writes_failure_status_using_enum_template(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a partitioned table with a failed partition.
        WHEN: upsert_freshness is called.
        THEN: the freshness write uses the shared status enum template with the failure value.
        """
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        attempted_at = Instant.now()

        upsert_freshness(postgres, table, {"10"}, attempted_at, success=False)

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/freshness_status", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone() == ("failure",)

    def test_full_rebuild_freshness_resets_to_current_manifest(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a full rebuild plan with current partitions.
        WHEN: update_published_freshness is called.
        THEN: freshness is reset to the complete current manifest.
        """
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
        attempted_at = Instant.now()

        update_published_freshness(postgres, table, plan, set(), attempted_at)

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/freshness_partitions", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall() == [("10", "success")]

    def test_incremental_freshness_records_success_failure_and_removal(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a partial partition publication with successful, failed, and removed partitions.
        WHEN: update_published_freshness is called.
        THEN: freshness matches each result with its correct status.
        """
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
        attempted_at = Instant.now()

        with (
            patch("dp.freshness.upsert_freshness") as upsert,
            patch("dp.freshness.delete_freshness") as delete,
        ):
            update_published_freshness(postgres, table, plan, {"20"}, attempted_at)

        assert upsert.call_args_list == [
            call(postgres, table, {"10"}, attempted_at, success=True),
            call(postgres, table, {"20"}, attempted_at, success=False),
        ]
        delete.assert_called_once_with(postgres, table, {"30"})

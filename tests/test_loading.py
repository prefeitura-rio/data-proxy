"""Tests for Parquet-to-PostgreSQL loading operations."""

from unittest.mock import ANY, patch

from duckdb import connect
from psycopg import Connection
from whenever import Instant

from dp.loading import apply_sync_plan
from dp.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    SyncPlan,
    TimeRangeSelection,
    UnitMapping,
)
from dp.publication import (
    partition_predicate,
    prepare_tables,
    publish_prepared_tables,
    reduce_sync_plan,
)
from dp.templates import TemplateSpec
from tests.helpers import partition, sync_config


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
        config = sync_config([FullTable(name="p.app.changed")])
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
        config = sync_config(
            [FullTable(name="p.app.changed"), FullTable(name="p.app.unchanged")]
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
                sync_config([table]),
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
                sync_config([table]),
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
        config = sync_config(
            [
                FullTable(
                    name="p.app.changed",
                    rls=[UnitMapping(column="id_cras", unit_type="cras")],
                )
            ],
            claim="preferred_username",
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
        config = sync_config([FullTable(name="p.app.changed")])
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
                sync_config([table]),
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
        config = sync_config([FullTable(name="p.app.changed")])
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

    def test_apply_sync_plan_records_preparation_failure_for_eligible_table(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: an eligible table that fails to prepare.
        WHEN: apply_sync_plan runs.
        THEN: it records the preparation failure without publishing.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )

        with (
            patch("dp.loading.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.record_table_failures") as record_failures,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
        ):
            result = apply_sync_plan(postgres, connect(":memory:"), config, plan)

        prepare.assert_called_once_with(postgres, ANY, ANY, ANY, {"p.app.changed"})
        record_failures.assert_called_with(postgres, [config.tables[0]], plan, ANY)
        assert result.published_tables == set()

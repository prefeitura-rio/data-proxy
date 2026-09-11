"""Tests for Parquet-to-PostgreSQL loading operations."""

from dataclasses import dataclass
from unittest.mock import ANY, MagicMock, patch

import pytest
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
from dp.settings import settings
from tests.conftest import PostgresTestNamespace
from tests.helpers import execute_sql, partition, sync_config


@dataclass(frozen=True, slots=True)
class PredicateCase:
    """One partition predicate rendering scenario."""

    name: str
    partition: PhysicalPartition
    expected: list[str]


class TestLoadingPartitionPredicate:
    """Tests for PartitionPredicate behavior."""

    @pytest.mark.parametrize(
        "case",
        [
            PredicateCase(
                "remainder",
                PhysicalPartition(
                    partition_id="__NULL__",
                    signature="signature",
                    selection=RemainderSelection(column="cpf", start=0, end=100),
                ),
                ['"cpf" IS NULL', '"cpf" < 0', '"cpf" >= 100'],
            ),
            PredicateCase(
                "bounded range",
                PhysicalPartition(
                    partition_id="10",
                    signature="signature",
                    selection=RangeSelection(
                        partition_id="10", column="cpf", lower=10, upper=20
                    ),
                ),
                ['"cpf" >= 10', '"cpf" < 20'],
            ),
            PredicateCase(
                "time range",
                PhysicalPartition(
                    partition_id="20250101",
                    signature="signature",
                    selection=TimeRangeSelection(
                        column="dt", lower="2025-01-01", upper="2025-01-02"
                    ),
                ),
                ["\"dt\" >= '2025-01-01'", "\"dt\" < '2025-01-02'"],
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_partition_predicate_renders_correct_predicates(
        self, case: PredicateCase
    ) -> None:
        """
        GIVEN: a physical partition with a specific selection type.
        WHEN: partition_predicate is rendered.
        THEN: the rendered SQL contains the expected predicates.
        """
        rendered = partition_predicate(case.partition).as_string(None)
        for expected in case.expected:
            assert expected in rendered


class TestLoadingPrepareTablesPaths:
    """Tests for planned table paths."""

    def test_prepare_tables_works_without_duckdb_connection(
        self,
    ) -> None:
        """
        GIVEN: a changed table with no entry in the plan paths.
        WHEN: prepare_tables runs without a duckdb connection.
        THEN: it returns no prepared tables.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(schema_name="app")

        postgres = MagicMock(spec=Connection)
        postgres.execute.side_effect = RuntimeError("DuckDB is unavailable")
        prepared = prepare_tables(postgres, config, plan, {"p.app.changed"})

        assert prepared == []

    def test_prepare_tables_skips_table_with_missing_paths(
        self,
    ) -> None:
        """
        GIVEN: a changed table with no entry in the plan paths.
        WHEN: prepare_tables runs.
        THEN: it returns no prepared tables.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(schema_name="app")

        postgres = MagicMock(spec=Connection)
        postgres.execute.side_effect = RuntimeError("DuckDB is unavailable")
        prepared = prepare_tables(postgres, config, plan, {"p.app.changed"})

        assert prepared == []

    def test_prepare_tables_uses_exact_planned_paths(
        self,
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

        rendered: list[tuple[str, str]] = []

        def render(template: str, mapping: object, **_: object) -> str:
            rendered.append((template, str(mapping)))
            return "SELECT 1"

        with (
            patch("dp.templates.render_template", side_effect=render),
            patch("dp.publication.bootstrap_table") as bootstrap,
            patch("dp.publication.cast_json_columns_to_jsonb"),
        ):
            prepared = prepare_tables(
                MagicMock(spec=Connection), config, plan, {"p.app.changed"}
            )

        bootstrap.assert_called_once_with(
            ANY,
            "app",
            "changed__next",
            None,
            None,
        )
        assert [table.name for table in prepared] == ["p.app.changed"]
        assert f"'{path}'" in dict(rendered)["postgres/create_table_from_parquet"]


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
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: an existing partitioned table with data in partitions 10, 20, and 30.
        WHEN: prepare_tables runs incrementally with changed partition 10 and removed partition 20.
        THEN: partition 10 is replaced, partition 20 is deleted, partition 30 is unchanged.
        """
        table = PartitionedTable(
            name=f"p.{namespace.schema}.people", resolved_schema=namespace.schema
        )
        changed = partition("10")
        removed = partition("20")
        kept = partition("30")
        path = "/test-files/people_partition_10.parquet"
        plan = SyncPlan(
            schema_name=namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": changed, "30": kept},
                    changed_paths={"10": path},
                    removed_partitions={"20": removed},
                )
            },
        )

        execute_sql(
            postgres,
            "postgres/create_people_table",
            mapping={"schema": namespace.schema},
        )
        execute_sql(
            postgres,
            "postgres/insert_people_rows",
            mapping={
                "schema": namespace.schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'old20'), (21, 'old21'), (30, 'keep30'), (31, 'keep31')",
            },
        )
        postgres.commit()

        prepared = prepare_tables(
            postgres,
            sync_config([table], schema_name=namespace.schema),
            plan,
            {table.name},
        )

        remaining = execute_sql(
            postgres,
            "postgres/select_people_rows",
            mapping={"schema": namespace.schema},
        ).fetchall()

        assert prepared == [table]
        assert remaining == [
            (10, "name10"),
            (11, "name11"),
            (12, "name12"),
            (13, "name13"),
            (14, "name14"),
            (15, "name15"),
            (16, "name16"),
            (17, "name17"),
            (18, "name18"),
            (19, "name19"),
            (30, "keep30"),
            (31, "keep31"),
        ]

    def test_prepare_tables_incremental_failure_rolls_back_only_failed_partition(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: an existing partitioned table with data in partitions 10, 20, and 30.
        WHEN: prepare_tables runs incrementally but partition 20's Parquet path does not exist.
        THEN: partitions 10 and 30 are updated, partition 20 retains its original data.
        """
        table = PartitionedTable(
            name=f"p.{namespace.schema}.people", resolved_schema=namespace.schema
        )
        changed_10 = partition("10")
        changed_20 = partition("20")
        kept = partition("30")
        path_10 = "/test-files/people_partition_10.parquet"
        path_20_missing = "/test-files/nonexistent.parquet"
        plan = SyncPlan(
            schema_name=namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={
                        "10": changed_10,
                        "20": changed_20,
                        "30": kept,
                    },
                    changed_paths={"10": path_10, "20": path_20_missing},
                    removed_partitions={},
                )
            },
        )

        execute_sql(
            postgres,
            "postgres/create_people_table",
            mapping={"schema": namespace.schema},
        )
        execute_sql(
            postgres,
            "postgres/insert_people_rows",
            mapping={
                "schema": namespace.schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'old20'), (21, 'old21'), (30, 'keep30'), (31, 'keep31')",
            },
        )
        postgres.commit()

        prepared = prepare_tables(
            postgres,
            sync_config([table], schema_name=namespace.schema),
            plan,
            {table.name},
        )

        remaining = execute_sql(
            postgres,
            "postgres/select_people_rows",
            mapping={"schema": namespace.schema},
        ).fetchall()

        assert prepared == [table]
        assert remaining == [
            (10, "name10"),
            (11, "name11"),
            (12, "name12"),
            (13, "name13"),
            (14, "name14"),
            (15, "name15"),
            (16, "name16"),
            (17, "name17"),
            (18, "name18"),
            (19, "name19"),
            (20, "old20"),
            (21, "old21"),
            (30, "keep30"),
            (31, "keep31"),
        ]

    def test_prepare_tables_incremental_skips_failed_removed_partition(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: an existing table with a removed partition that references a non-existent column.
        WHEN: prepare_tables runs incrementally.
        THEN: the failed delete is rolled back and changed partitions still succeed.
        """
        table = PartitionedTable(
            name=f"p.{namespace.schema}.people", resolved_schema=namespace.schema
        )
        changed_10 = partition("10")
        bad_removed = PhysicalPartition(
            partition_id="99",
            signature="signature",
            selection=RangeSelection(
                partition_id="99",
                column="nonexistent",
                lower=0,
                upper=100,
            ),
        )
        path_10 = "/test-files/people_partition_10.parquet"
        plan = SyncPlan(
            schema_name=namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": changed_10},
                    changed_paths={"10": path_10},
                    removed_partitions={"99": bad_removed},
                )
            },
        )

        execute_sql(
            postgres,
            "postgres/create_people_table",
            mapping={"schema": namespace.schema},
        )
        execute_sql(
            postgres,
            "postgres/insert_people_rows",
            mapping={
                "schema": namespace.schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'keep20')",
            },
        )
        postgres.commit()

        prepared = prepare_tables(
            postgres,
            sync_config([table], schema_name=namespace.schema),
            plan,
            {table.name},
        )

        remaining = execute_sql(
            postgres,
            "postgres/select_people_rows",
            mapping={"schema": namespace.schema},
        ).fetchall()

        assert prepared == [table]
        assert remaining == [
            (10, "name10"),
            (11, "name11"),
            (12, "name12"),
            (13, "name13"),
            (14, "name14"),
            (15, "name15"),
            (16, "name16"),
            (17, "name17"),
            (18, "name18"),
            (19, "name19"),
            (20, "keep20"),
        ]

    def test_prepare_tables_full_rebuilds_partitioned_from_parquet(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a full-rebuild partitioned table.
        WHEN: prepare_tables runs.
        THEN: the table starts from Parquet instead of a live copy.
        """
        table = PartitionedTable(
            name=f"p.{namespace.schema}.people", resolved_schema=namespace.schema
        )
        current_partition = partition("10")
        path = "s3://bucket/app/people/partitions/10/data.parquet"
        plan = SyncPlan(
            schema_name=namespace.schema,
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
        rendered: list[str] = []
        mappings: list[object] = []

        def render(path: str, mapping: object) -> str:
            rendered.append(path)
            mappings.append(mapping)
            return "SELECT 1"

        with (
            patch("dp.templates.render_template", side_effect=render),
            patch("dp.publication.bootstrap_table"),
            patch("dp.publication.cast_json_columns_to_jsonb"),
        ):
            prepared = prepare_tables(
                postgres,
                sync_config([table]),
                plan,
                {table.name},
            )

        assert "postgres/create_table_from_parquet" in rendered
        assert "s3://bucket/app/people/partitions/10/data.parquet" in str(mappings[0])
        assert "*" not in str(mappings[0])
        assert prepared == [table]

    def test_prepare_tables_secures_shadow_before_load(
        self,
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
        calls: list[str] = []

        def record_bootstrap(*_: object) -> None:
            calls.append("bootstrap")

        def record_cast(*_: object) -> None:
            calls.append("cast")

        with (
            patch("dp.templates.render_template", return_value="SELECT 1"),
            patch("dp.publication.bootstrap_table", side_effect=record_bootstrap),
            patch("dp.publication.cast_json_columns_to_jsonb", side_effect=record_cast),
        ):
            prepare_tables(MagicMock(spec=Connection), config, plan, {"p.app.changed"})

        assert calls == ["bootstrap", "cast"]


class TestLoadingPublishPrepared:
    """Tests for PublishPrepared behavior."""

    def test_publish_prepared_tables_swaps_each_table(
        self,
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
                (MagicMock(spec=Connection)),
                tables,
                plan,
                {},
                Instant.now(),
            )

        assert publish.call_count == 2
        assert result == {"p.app.one", "p.app.two"}

    def test_publish_prepared_tables_skips_swap_for_incremental(
        self,
    ) -> None:
        """
        GIVEN: an incremental partitioned table that was prepared.
        WHEN: publish_prepared_tables runs.
        THEN: it skips the table swap and only updates freshness.
        """
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "s3://bucket/10.parquet"},
                    removed_partitions={},
                )
            },
        )

        with (
            patch("dp.publication.publish_table") as publish,
            patch("dp.publication.update_published_freshness") as freshness,
        ):
            result = publish_prepared_tables(
                MagicMock(spec=Connection),
                [table],
                plan,
                {},
                Instant.now(),
            )

        publish.assert_not_called()
        freshness.assert_called_once()
        assert result == {"p.app.people"}

    def test_publish_prepared_tables_excludes_failed_publication(
        self,
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
                (MagicMock(spec=Connection)),
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

        with (
            patch("dp.loading.initialize_schemas") as initialize,
            patch("dp.loading.record_extraction_failures"),
            patch("dp.loading.prepare_tables", return_value=[config.tables[0]]),
            patch(
                "dp.loading.publish_prepared_tables",
                return_value={"p.app.changed"},
            ) as publish,
            patch("dp.loading.reload_postgrest") as reload,
            patch("dp.loading.create_bq_views"),
        ):
            result = apply_sync_plan(MagicMock(spec=Connection), config, plan)

        initialize.assert_called_once()
        publish.assert_called_once()
        reload.assert_called_once()
        assert result.plan == plan
        assert result.published_tables == {"p.app.changed"}

    def test_apply_sync_plan_publishes_silo_parquet(
        self,
        postgres_silo: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """The real orchestration publishes the Silo-backed Parquet fixture."""
        table = FullTable(
            name=f"p.{namespace.schema}.people", resolved_schema=namespace.schema
        )
        plan = SyncPlan(
            schema_name=namespace.schema,
            signatures={table.name: "sig"},
            paths={
                table.name: [f"s3://test-bucket/{namespace.schema}/people/data.parquet"]
            },
        )
        result = apply_sync_plan(
            postgres_silo, sync_config([table], schema_name=namespace.schema), plan
        )
        assert result.published_tables == {table.name}
        assert execute_sql(
            postgres_silo,
            "postgres/select_people_rows",
            mapping={"schema": namespace.schema},
        ).fetchone() == (10, "name10")

    def test_apply_sync_plan_creates_fallback_views_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Enabled fallback creates BigQuery views after publication."""
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(schema_name="app")
        monkeypatch.setattr(settings, "FALLBACK_ENABLED", True)
        with (
            patch("dp.loading.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]),
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
            patch("dp.loading.create_bq_views") as create_views,
        ):
            conn = MagicMock(spec=Connection)
            apply_sync_plan(conn, config, plan)

        create_views.assert_called_once_with(conn, config)

    def test_apply_sync_plan_records_failure_without_incremental_publication(
        self,
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
            patch("dp.loading.create_bq_views"),
        ):
            result = apply_sync_plan(
                MagicMock(spec=Connection),
                sync_config([table]),
                plan,
                {path},
            )

        prepare.assert_called_once_with(ANY, ANY, ANY, set())
        record_failures.assert_called_once_with(
            ANY, [table], plan, ANY, {table.name: {"10"}}
        )
        assert result.published_tables == set()

    def test_apply_sync_plan_excludes_extraction_failures(
        self,
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
            patch("dp.loading.record_extraction_failures"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
            patch("dp.loading.create_bq_views"),
        ):
            result = apply_sync_plan(
                MagicMock(spec=Connection),
                config,
                plan,
                {"s3://bucket/changed/data.parquet"},
            )

        prepare.assert_called_once_with(ANY, config, plan, set())
        assert result.plan == plan
        assert result.published_tables == set()

    def test_apply_sync_plan_records_preparation_failure_for_eligible_table(
        self,
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
            patch("dp.loading.record_extraction_failures"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.record_table_failures") as record_failures,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.reload_postgrest"),
            patch("dp.loading.create_bq_views"),
        ):
            result = apply_sync_plan(MagicMock(spec=Connection), config, plan)

        prepare.assert_called_once_with(ANY, ANY, ANY, {"p.app.changed"})
        record_failures.assert_called_with(ANY, [config.tables[0]], plan, ANY)
        assert result.published_tables == set()

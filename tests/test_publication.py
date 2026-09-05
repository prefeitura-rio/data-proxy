"""Tests for publication input validation and SQL behavior."""

from typing import cast
from unittest.mock import patch

import pytest
from duckdb import connect
from psycopg import Connection
from psycopg.sql import Composable

from dp.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    SyncPlan,
)
from dp.publication import (
    cast_json_columns_to_jsonb,
    create_incremental_shadow,
    create_indexes,
    load_table,
    partition_predicate,
    planned_paths,
    publish_table,
    reduce_sync_plan,
)
from dp.templates import TemplateSpec
from tests.helpers import execute_sql, execute_template, partition


class TestPublication:
    """Tests for publication input validation."""

    def test_planned_paths_rejects_an_invalid_partition_plan(
        self,
        invalid_partition_plan: PartitionedTablePlan,
    ) -> None:
        """
        GIVEN: an invalid partition plan value.
        WHEN: planned_paths is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            planned_paths(
                SyncPlan(schema_name="app"),
                "p.d.t",
                invalid_partition_plan,
            )

    def test_partition_predicate_rejects_an_invalid_selection_type(
        self,
        invalid_physical_partition: PhysicalPartition,
    ) -> None:
        """
        GIVEN: a physical partition with an invalid selection type.
        WHEN: partition_predicate is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            partition_predicate(invalid_physical_partition)


class TestPublicationTemplates:
    """Tests for publication SQL and plan reduction behavior."""

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

        assert duckdb.execute("SELECT 1").fetchone() == (1,)

    def test_publish_table_swaps_before_index_creation(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a prepared shadow table with an index configuration.
        WHEN: publish_table is called.
        THEN: the table is swapped before the index is created.
        """
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_table",
                mapping={
                    "schema": "app",
                    "table": "table__next",
                    "columns": "id int",
                },
            ),
        )
        table = FullTable(
            name="p.app.table",
            resolved_schema="app",
            indexes=[IndexConfig(name="idx_table", columns=["id"])],
        )

        publish_table(postgres, table)

        assert execute_sql(
            postgres, "postgres/regclass_table_and_shadow"
        ).fetchone() == ('app."table"', None)
        assert execute_sql(postgres, "postgres/index_names").fetchall() == [
            ("idx_table",)
        ]

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

        assert decision.plan == plan
        assert decision.blocked_tables == set()
        assert decision.failed_partitions == {}

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

    def test_create_indexes_creates_btree_index_for_columns(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a table with an index config using only columns.
        WHEN: create_indexes is called.
        THEN: a plain B-tree index is created on those columns.
        """
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_table",
                mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": "id int",
                },
            ),
        )

        table = FullTable(
            name="p.app.table",
            resolved_schema="app",
            indexes=[IndexConfig(name="idx_id", columns=["id"])],
        )

        create_indexes(postgres, table, "table")

        assert execute_sql(postgres, "postgres/index_names").fetchall() == [("idx_id",)]

    def test_create_indexes_creates_gin_index_for_expressions(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a table with a jsonb column and a gin index config using expressions.
        WHEN: create_indexes is called.
        THEN: a GIN index is created on the JSON path expression.
        """
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_table",
                mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": "data jsonb",
                },
            ),
        )
        table = FullTable(
            name="p.app.table",
            resolved_schema="app",
            indexes=[
                IndexConfig(
                    name="idx_data_status",
                    columns=["data"],
                    method="gin",
                    expressions=["(data->'status')"],
                )
            ],
        )

        create_indexes(postgres, table, "table")

        result = postgres.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'app' AND tablename = 'table'"
        ).fetchall()

        assert result == [("idx_data_status",)]

        indexdef = postgres.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_data_status'"
        ).fetchone()
        assert indexdef is not None
        assert cast(str, indexdef[0]).endswith("USING gin (((data -> 'status'::text)))")

    @pytest.mark.parametrize(
        ("columns", "expected"),
        [
            (
                "id int, data json, name text",
                [("data", "jsonb"), ("id", "integer"), ("name", "text")],
            ),
            ("id int, name text", [("id", "integer"), ("name", "text")]),
        ],
        ids=["with json", "without json"],
    )
    def test_cast_json_columns_to_jsonb(
        self,
        postgres: Connection[tuple[object, ...]],
        columns: str,
        expected: list[tuple[str, str]],
    ) -> None:
        """
        GIVEN: a table with or without json columns.
        WHEN: cast_json_columns_to_jsonb is called.
        THEN: json columns become jsonb and other columns are unchanged.
        """
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_table",
                mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": columns,
                },
            ),
        )

        cast_json_columns_to_jsonb(postgres, "app", "table")

        result = postgres.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'table' ORDER BY column_name"
        ).fetchall()
        assert result == expected

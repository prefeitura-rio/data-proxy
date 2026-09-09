"""Tests for publication input validation and SQL behavior."""

from typing import cast
from unittest.mock import patch

import pytest
from psycopg import Connection

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
    column_select_list,
    create_indexes,
    create_shadow_from_parquet,
    delete_partitions,
    load_partition,
    partition_predicate,
    planned_paths,
    publish_table,
    reduce_sync_plan,
)
from tests.helpers import execute_sql, partition


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

    def test_delete_partitions_renders_predicate_and_delete(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a table with changed physical partitions.
        WHEN: delete_partitions is called.
        THEN: it renders partition predicates and a delete statement.
        """
        rendered: list[str] = []

        def render(path: str, mapping: object, **_: object) -> str:
            rendered.append(path)
            return "SELECT 1"

        with (
            patch("dp.publication.render_template", side_effect=render),
            patch("dp.templates.render_template", side_effect=render),
        ):
            delete_partitions(
                postgres,
                PartitionedTable(name="p.app.people"),
                [partition("10"), partition("20")],
            )

        assert rendered == [
            "postgres/partition_range_predicate",
            "postgres/partition_range_predicate",
            "postgres/delete_partitions",
        ]

    def test_create_shadow_from_parquet_uses_glob_path(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a table and a glob path.
        WHEN: create_shadow_from_parquet is called with a glob path string.
        THEN: it renders postgres/create_table_from_parquet with the full glob.
        """
        captured: dict[str, object] = {}

        def render(path: str, mapping: object, **_: object) -> str:
            captured["template"] = path
            captured["mapping"] = mapping
            return "SELECT 1"

        with (
            patch("dp.publication.render_template", side_effect=render),
            patch("dp.templates.render_template", side_effect=render),
        ):
            create_shadow_from_parquet(
                postgres,
                FullTable(name="p.app.table", resolved_schema="app"),
                "table__next",
                "s3://bucket/app/table/*/data.parquet",
            )

        assert captured["template"] == "postgres/create_table_from_parquet"
        assert "'s3://bucket/app/table/*/data.parquet'" in str(captured["mapping"])

    def test_load_partition_inserts_via_read_parquet(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a table and a Parquet file with matching columns.
        WHEN: load_partition is called.
        THEN: the Parquet data is inserted into the table.
        """
        postgres.execute("CREATE TABLE app.people (cpf int, name text)")
        postgres.commit()

        select_list = column_select_list(postgres, "app", "people")
        load_partition(
            postgres,
            "app",
            "people",
            "/test-files/people_partition_10.parquet",
            select_list,
        )
        postgres.commit()

        rows = postgres.execute(
            "SELECT cpf, name FROM app.people ORDER BY cpf"
        ).fetchall()
        assert rows == [
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
        ]

    def test_publish_table_creates_indexes_before_swap(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a prepared shadow table with an index configuration.
        WHEN: publish_table is called.
        THEN: indexes are created on the shadow before the swap.
        """
        calls: list[str] = []

        def record_indexes(*_: object) -> None:
            calls.append("indexes")

        def record_swap(*args: object, **kwargs: object) -> None:
            calls.append("swap")

        table = FullTable(
            name="p.app.table",
            resolved_schema="app",
            indexes=[IndexConfig(name="idx_table", columns=["id"])],
        )

        with (
            patch("dp.publication.create_indexes", side_effect=record_indexes),
            patch("dp.publication.execute_sql", side_effect=record_swap),
        ):
            publish_table(postgres, table)

        assert calls == ["indexes", "swap"]

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
        execute_sql(postgres, "postgres/create_table", mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": "id int",
                })

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
        execute_sql(postgres, "postgres/create_table", mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": "data jsonb",
                })
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
        execute_sql(postgres, "postgres/create_table", mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": columns,
                })

        cast_json_columns_to_jsonb(postgres, "app", "table")

        result = postgres.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'app' AND table_name = 'table' ORDER BY column_name"
        ).fetchall()
        assert result == expected

"""Tests for publication input validation and SQL behavior."""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from psycopg import Connection, Cursor
from psycopg.sql import SQL

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
from tests.conftest import PostgresTestNamespace
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
                MagicMock(spec=Connection),
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
                MagicMock(spec=Connection),
                FullTable(name="p.app.table", resolved_schema="app"),
                "table__next",
                "s3://bucket/app/table/*/data.parquet",
            )

        assert captured["template"] == "postgres/create_table_from_parquet"
        assert "'s3://bucket/app/table/*/data.parquet'" in str(captured["mapping"])

    def test_load_partition_inserts_via_read_parquet(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a table and a Parquet file with matching columns.
        WHEN: load_partition is called.
        THEN: the Parquet data is inserted into the table.
        """
        execute_sql(
            postgres,
            "postgres/create_people_table",
            mapping={"schema": namespace.schema},
        )
        postgres.commit()

        select_list = column_select_list(postgres, namespace.schema, "people")
        load_partition(
            postgres,
            namespace.schema,
            "people",
            "/test-files/people_partition_10.parquet",
            select_list,
        )
        postgres.commit()

        rows = execute_sql(
            postgres,
            "postgres/select_people_rows",
            mapping={"schema": namespace.schema},
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

    def test_load_partition_converts_json_to_jsonb(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: an existing partitioned table with a JSONB column.
        WHEN: one Parquet partition is loaded incrementally.
        THEN: PostgreSQL stores the JSON content as JSONB.
        """
        postgres.execute(
            SQL("CREATE TABLE {}.people (cpf integer, data jsonb)").format(
                namespace.identifier
            )
        )
        postgres.commit()

        select_list = column_select_list(postgres, namespace.schema, "people")
        load_partition(
            postgres,
            namespace.schema,
            "people",
            "/test-files/json_partition_10.parquet",
            select_list,
        )
        postgres.commit()

        rows = postgres.execute(
            SQL("SELECT cpf, data::text, pg_typeof(data)::text FROM {}.people").format(
                namespace.identifier
            )
        ).fetchall()
        assert rows == [(10, '{"source": "fixture"}', "jsonb")]

    def test_publish_table_creates_indexes_before_swap(
        self,
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
            publish_table(MagicMock(spec=Connection), table)

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
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a table with an index config using only columns.
        WHEN: create_indexes is called.
        THEN: a plain B-tree index is created on those columns.
        """
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={
                "schema": namespace.schema,
                "table": "table",
                "columns": "id int",
            },
        )

        table = FullTable(
            name=f"p.{namespace.schema}.table",
            resolved_schema=namespace.schema,
            indexes=[IndexConfig(name="idx_id", columns=["id"])],
        )

        create_indexes(postgres, table, "table")

        assert execute_sql(
            postgres,
            "postgres/index_names",
            mapping={"schema": namespace.schema, "table": "table"},
        ).fetchall() == [("idx_id",)]

    def test_create_indexes_creates_gin_index_for_expressions(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a table with a jsonb column and a gin index config using expressions.
        WHEN: create_indexes is called.
        THEN: a GIN index is created on the JSON path expression.
        """
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={
                "schema": namespace.schema,
                "table": "table",
                "columns": "data jsonb",
            },
        )
        table = FullTable(
            name=f"p.{namespace.schema}.table",
            resolved_schema=namespace.schema,
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

        result = execute_sql(
            postgres,
            "postgres/index_names",
            mapping={"schema": namespace.schema, "table": "table"},
        ).fetchall()

        assert result == [("idx_data_status",)]

        indexdef = execute_sql(
            postgres,
            "postgres/index_definition",
            mapping={"schema": namespace.schema},
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
        namespace: PostgresTestNamespace,
        columns: str,
        expected: list[tuple[str, str]],
    ) -> None:
        """
        GIVEN: a table with or without json columns.
        WHEN: cast_json_columns_to_jsonb is called.
        THEN: json columns become jsonb and other columns are unchanged.
        """
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={
                "schema": namespace.schema,
                "table": "table",
                "columns": columns,
            },
        )

        cast_json_columns_to_jsonb(postgres, namespace.schema, "table")

        result = execute_sql(
            postgres,
            "postgres/table_column_types",
            mapping={"schema": namespace.schema, "table": "table"},
        ).fetchall()
        assert result == expected

    def test_cast_json_columns_to_jsonb_uses_one_statement(
        self,
    ) -> None:
        """
        GIVEN: a table with two json columns.
        WHEN: cast_json_columns_to_jsonb is called.
        THEN: one statement carries both columns, because each one costs a rewrite.
        """
        calls: list[tuple[str, dict[str, object]]] = []

        def record(
            _: object,
            path: str,
            mapping: dict[str, object] | None = None,
            **__: object,
        ) -> Cursor[tuple[object, ...]]:
            calls.append((path, mapping or {}))
            cursor = MagicMock(spec=Cursor)
            cursor.fetchall.return_value = [("first",), ("second",)]
            return cast(Cursor[tuple[object, ...]], cursor)

        with patch("dp.publication.execute_sql", side_effect=record):
            cast_json_columns_to_jsonb(MagicMock(spec=Connection), "app", "table")

        assert [path for path, _ in calls] == [
            "postgres/json_columns",
            "postgres/cast_json_to_jsonb",
        ]

        clauses = str(calls[1][1]["clauses"])

        assert clauses.count("ALTER COLUMN") == 2
        assert clauses.count("::jsonb") == 2
        assert "first" in clauses
        assert "second" in clauses

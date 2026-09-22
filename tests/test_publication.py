"""Tests for publication conditions, plan reduction, and table lifecycle."""

from unittest.mock import AsyncMock, patch

import pytest
from psycopg.sql import SQL, Identifier

from dp.conditions import partition_condition, scan_condition
from dp.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    PhysicalPartition,
    RemainderSelection,
    SyncPlan,
)
from dp.publication import (
    CreateRoute,
    ReplacePartitionsRoute,
    ShadowSwapRoute,
    cast_json_columns_to_jsonb,
    create_indexes,
    decide_route,
    planned_paths,
    prepare_table,
    prepare_tables,
    publish_table,
    reduce_sync_plan,
)
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, fetch_all, partition, sync_config


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

    def test_partition_condition_rejects_an_invalid_selection_type(
        self,
        invalid_physical_partition: PhysicalPartition,
    ) -> None:
        """
        GIVEN: a physical partition with an invalid selection type.
        WHEN: partition_condition is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            partition_condition(invalid_physical_partition)


class TestConditions:
    """SQL condition generation for partitions."""

    def test_partition_condition_covers_the_remainder_bucket(self) -> None:
        """
        GIVEN: a remainder partition selection.
        WHEN: partition_condition is called.
        THEN: it matches null and out-of-range values with the identifier form.
        """
        remainder = PhysicalPartition(
            partition_id="__NULL__",
            signature="signature",
            selection=RemainderSelection(column="cpf", start=0, end=100),
        )
        rendered = partition_condition(remainder).as_string(None)
        assert '"cpf" IS NULL' in rendered
        assert '"cpf" >= 100' in rendered

    def test_scan_condition_uses_parquet_columns(self) -> None:
        """
        GIVEN: an integer range partition.
        WHEN: scan_condition is called.
        THEN: it reads the partition column from the Parquet record form.
        """
        rendered = scan_condition(partition("10")).as_string(None)
        assert "r['cpf']" in rendered


class TestReduceSyncPlan:
    """Plan reduction behavior."""

    def test_keeps_a_plan_without_failures(self) -> None:
        """
        GIVEN: a plan whose partitions did not fail.
        WHEN: reduce_sync_plan is called.
        THEN: nothing is blocked and no partition is recorded as failed.
        """
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.people": PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "ok"},
                    removed_partitions={},
                )
            },
        )

        decision = reduce_sync_plan(plan, set())

        assert decision.blocked_tables == set()
        assert decision.failed_partitions == {}

    def test_blocks_a_failed_full_rebuild(self) -> None:
        """
        GIVEN: a full rebuild with a failed partition path.
        WHEN: reduce_sync_plan is called.
        THEN: the whole table is blocked.
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


class TestCastJsonColumns:
    """JSON-to-JSONB conversion."""

    @pytest.mark.asyncio
    async def test_converts_json_columns_to_jsonb(self, postgres: Postgres) -> None:
        """
        GIVEN: a table with a json column.
        WHEN: cast_json_columns_to_jsonb is called.
        THEN: the column type becomes jsonb.
        """
        schema = postgres.namespace.schema
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "json_t",
                "columns": "id integer, data json",
            },
        )
        await postgres.connection.commit()

        await cast_json_columns_to_jsonb(postgres.connection, schema, "json_t")

        rows = await fetch_all(
            postgres.connection,
            "postgres/table_column_types",
            mapping={"schema": schema, "table": "json_t"},
        )
        assert rows == [("data", "jsonb"), ("id", "integer")]


class TestPublishTable:
    """Shadow-to-live table swap."""

    @pytest.mark.asyncio
    async def test_swaps_the_shadow_into_service(self, postgres: Postgres) -> None:
        """
        GIVEN: a live table, a shadow table, and one configured btree index.
        WHEN: publish_table is called.
        THEN: the shadow rows become live and the index exists.
        """
        schema = postgres.namespace.schema
        table = FullTable(
            name=f"p.{schema}.people",
            resolved_schema=schema,
            indexes=[IndexConfig(name="idx_people_cpf", columns=["cpf"])],
        )
        await execute_sql(
            postgres.connection,
            "postgres/create_people_table",
            mapping={"schema": schema},
        )
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "people__next",
                "columns": "cpf integer, name text",
            },
        )
        await postgres.connection.execute(
            SQL("INSERT INTO {} VALUES (20, 'new20')").format(
                Identifier(schema, "people__next")
            )
        )
        await postgres.connection.commit()

        await publish_table(postgres.connection, table)
        await postgres.connection.commit()

        rows = await fetch_all(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": schema},
        )
        indexes = await fetch_all(
            postgres.connection,
            "postgres/index_names",
            mapping={"schema": schema, "table": "people"},
        )
        assert rows == [(20, "new20")]
        assert indexes == [("idx_people_cpf",)]

    @pytest.mark.asyncio
    async def test_publish_table_preserves_indexes_when_live_table_already_has_them(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a live table that already has an index from a previous sync,
               and a shadow table with new data.
        WHEN: publish_table is called.
        THEN: the index exists on the new live table after the swap.

        This reproduces the bug where CREATE INDEX IF NOT EXISTS on the
        shadow table was silently skipped because the index name already
        existed on the old live table.  After the swap, the old table
        (with the index) was dropped, leaving the new live table without
        any indexes.
        """
        schema = postgres.namespace.schema
        table = FullTable(
            name=f"p.{schema}.people",
            resolved_schema=schema,
            indexes=[IndexConfig(name="idx_people_cpf", columns=["cpf"])],
        )
        await execute_sql(
            postgres.connection,
            "postgres/create_people_table",
            mapping={"schema": schema},
        )
        await postgres.connection.execute(
            SQL("INSERT INTO {} VALUES (10, 'old10')").format(
                Identifier(schema, "people")
            )
        )
        # Create the index on the live table (simulating a previous sync)
        await postgres.connection.execute(
            SQL("CREATE INDEX idx_people_cpf ON {} ({})").format(
                Identifier(schema, "people"), Identifier("cpf")
            )
        )
        await postgres.connection.commit()

        # Create the shadow table with new data
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "people__next",
                "columns": "cpf integer, name text",
            },
        )
        await postgres.connection.execute(
            SQL("INSERT INTO {} VALUES (20, 'new20')").format(
                Identifier(schema, "people__next")
            )
        )
        await postgres.connection.commit()

        await publish_table(postgres.connection, table)
        await postgres.connection.commit()

        rows = await fetch_all(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": schema},
        )
        indexes = await fetch_all(
            postgres.connection,
            "postgres/index_names",
            mapping={"schema": schema, "table": "people"},
        )
        assert rows == [(20, "new20")]
        assert indexes == [("idx_people_cpf",)]

    @pytest.mark.asyncio
    async def test_create_indexes_supports_expression_indexes(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a table with a gin expression index.
        WHEN: create_indexes is called.
        THEN: the expression index exists on the table.
        """
        schema = postgres.namespace.schema
        table = FullTable(
            name=f"p.{schema}.people",
            resolved_schema=schema,
            indexes=[
                IndexConfig(
                    name="idx_people_name",
                    method="gin",
                    columns=["name"],
                    expressions=["to_tsvector('portuguese', name)"],
                )
            ],
        )
        await execute_sql(
            postgres.connection,
            "postgres/create_people_table",
            mapping={"schema": schema},
        )
        await postgres.connection.commit()

        await create_indexes(postgres.connection, table, "people")
        await postgres.connection.commit()

        indexes = await fetch_all(
            postgres.connection,
            "postgres/index_names",
            mapping={"schema": schema, "table": "people"},
        )
        assert indexes == [("idx_people_name",)]


class TestPrepareTable:
    """Table preparation behavior."""

    @pytest.mark.asyncio
    async def test_requires_an_existing_incremental_table(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: an incremental plan for a table that does not exist.
        WHEN: prepare_table is called.
        THEN: it raises RuntimeError naming the missing table.
        """
        schema = postgres.namespace.schema
        table = FullTable(name=f"p.{schema}.people", resolved_schema=schema)
        plan = SyncPlan(
            schema_name=schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "s3://b/10"},
                    removed_partitions={},
                )
            },
        )

        with pytest.raises(RuntimeError, match="incremental plan"):
            await prepare_table(
                postgres.connection,
                sync_config([table], schema_name=schema),
                table,
                plan,
                plan.partitioned_tables[table.name],
            )

    @pytest.mark.asyncio
    async def test_prepare_tables_skips_a_table_that_fails(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a table whose Parquet path does not exist.
        WHEN: prepare_tables runs.
        THEN: the failure is logged and the table is not prepared.
        """
        schema = postgres.namespace.schema
        table = FullTable(name=f"p.{schema}.people", resolved_schema=schema)
        plan = SyncPlan(
            schema_name=schema,
            signatures={table.name: "signature"},
            paths={table.name: ["s3://missing/data.parquet"]},
        )

        with patch("dp.publication.emit_error", new_callable=AsyncMock):
            prepared = await prepare_tables(
                postgres.connection,
                postgres.connection,
                sync_config([table], schema_name=schema),
                plan,
                {table.name},
            )

        assert prepared == []

    @pytest.mark.asyncio
    async def test_prepare_tables_ignores_tables_outside_the_changed_set(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a configured table that is not in the changed set.
        WHEN: prepare_tables runs.
        THEN: it skips the table without preparing anything.
        """
        schema = postgres.namespace.schema
        table = FullTable(name=f"p.{schema}.people", resolved_schema=schema)
        plan = SyncPlan(schema_name=schema)

        prepared = await prepare_tables(
            postgres.connection,
            postgres.connection,
            sync_config([table], schema_name=schema),
            plan,
            set(),
        )

        assert prepared == []


class TestDecideRoute:
    """Tests for the publication route decision."""

    def test_full_table_existing_uses_shadow_swap(self) -> None:
        """
        GIVEN: an existing full table.
        WHEN: decide_route runs.
        THEN: it returns SHADOW_SWAP.
        """
        table = FullTable(name="p.d.t")
        assert decide_route(True, table, None) == ShadowSwapRoute()

    def test_full_table_new_uses_create(self) -> None:
        """
        GIVEN: a new full table.
        WHEN: decide_route runs.
        THEN: it returns CREATE.
        """
        table = FullTable(name="p.d.t")
        assert decide_route(False, table, None) == CreateRoute()

    def test_partitioned_incremental_uses_replace_partitions(self) -> None:
        """
        GIVEN: an existing partitioned table with an incremental plan.
        WHEN: decide_route runs.
        THEN: it returns REPLACE_PARTITIONS.
        """
        table = PartitionedTable(name="p.d.t")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=False,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        assert decide_route(True, table, plan) == ReplacePartitionsRoute(plan=plan)

    def test_partitioned_full_rebuild_uses_shadow_swap(
        self,
    ) -> None:
        """
        GIVEN: an existing partitioned table and a full rebuild.
        WHEN: decide_route runs.
        THEN: it returns SHADOW_SWAP.
        """
        table = PartitionedTable(name="p.d.t")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        assert decide_route(True, table, plan) == ShadowSwapRoute()

    def test_partitioned_first_creation_uses_create(self) -> None:
        """
        GIVEN: a new partitioned table that does not exist yet.
        WHEN: decide_route runs.
        THEN: it returns CREATE.
        """
        table = PartitionedTable(name="p.d.t")
        plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        assert decide_route(False, table, plan) == CreateRoute()

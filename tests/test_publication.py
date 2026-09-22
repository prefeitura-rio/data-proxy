"""Tests for publication conditions, plan reduction, and table lifecycle."""

from unittest.mock import AsyncMock, patch

import pytest
from psycopg.sql import SQL, Identifier

from data_proxy.models import (
    FullTable,
    IndexConfig,
    PartitionedTablePlan,
    SyncPlan,
)
from data_proxy.publication import (
    cast_json_columns_to_jsonb,
    create_indexes,
    prepare_table,
    prepare_tables,
    publish_table,
)
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, fetch_all, partition, sync_config


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
        await postgres.connection.execute(
            SQL("CREATE INDEX idx_people_cpf ON {} ({})").format(
                Identifier(schema, "people"), Identifier("cpf")
            )
        )
        await postgres.connection.commit()
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
        with patch("data_proxy.publication.emit_error", new_callable=AsyncMock):
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

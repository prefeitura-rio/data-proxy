"""Tests for Parquet-to-PostgreSQL loading operations."""

from unittest.mock import patch

import pytest
from psycopg.sql import SQL

from data_proxy.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    SyncPlan,
)
from data_proxy.publication import (
    PreparedTable,
    prepare_tables,
    run_publication,
)
from tests.fixtures.types import Postgres
from tests.helpers import fetch_all, fetch_one, partition, sync_config


class TestLoadingRunPublication:
    """Tests for ApplySyncPlan behavior."""

    @pytest.mark.asyncio
    async def test_run_publication_publishes_silo_parquet(
        self, postgres: Postgres
    ) -> None:
        """The real orchestration publishes the Silo-backed Parquet fixture."""
        table = FullTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
        )
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            signatures={table.name: "sig"},
            paths={
                table.name: [
                    f"s3://test-bucket/{postgres.namespace.schema}/people/data.parquet"
                ]
            },
        )
        with patch("data_proxy.publication.run_fallback_views_creation"):
            result = await run_publication(
                postgres.connection,
                postgres.connection,
                sync_config([table], schema_name=postgres.namespace.schema),
                plan,
            )
        assert result.published_tables == {table.name}
        assert await fetch_one(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": postgres.namespace.schema},
        ) == (10, "name10")

    @pytest.mark.asyncio
    async def test_prepare_tables_creates_a_missing_full_table_directly(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a full table that does not exist yet.
        WHEN: prepare_tables runs against the real pg_duckdb instance.
        THEN: the live table appears with its rows and its index, and no swap runs.
        """
        table = FullTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
            indexes=[IndexConfig(name="idx_people_cpf", columns=["cpf"])],
        )
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            signatures={table.name: "sig"},
            paths={
                table.name: [
                    f"s3://test-bucket/{postgres.namespace.schema}/people/data.parquet"
                ]
            },
        )
        prepared = await prepare_tables(
            postgres.connection,
            postgres.connection,
            sync_config([table], schema_name=postgres.namespace.schema),
            plan,
            {table.name},
        )
        rows = await fetch_all(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": postgres.namespace.schema},
        )
        indexes = await fetch_all(
            postgres.connection,
            "postgres/index_names",
            mapping={"schema": postgres.namespace.schema, "table": "people"},
        )
        assert prepared == [PreparedTable(table=table, swap=False)]
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
        assert indexes == [("idx_people_cpf",)]

    @pytest.mark.asyncio
    async def test_prepare_tables_appends_every_batch_of_a_full_rebuild(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a full rebuild whose two batches hold different partitions.
        WHEN: prepare_tables runs against the real pg_duckdb instance.
        THEN: the shadow table holds the rows of both batches.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
        )
        first = f"s3://test-bucket/{postgres.namespace.schema}/people/data.parquet"
        second = "s3://test-bucket/app/people/people_partition_20.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="sig",
                    full_rebuild=True,
                    current_partitions={"10": partition("10"), "20": partition("20")},
                    changed_paths={"10": first, "20": second},
                    removed_partitions={},
                )
            },
        )
        prepared = await prepare_tables(
            postgres.connection,
            postgres.connection,
            sync_config([table], schema_name=postgres.namespace.schema),
            plan,
            {table.name},
        )
        cursor = await postgres.connection.execute(
            SQL("SELECT cpf, name FROM {} ORDER BY cpf").format(
                postgres.namespace.table("people")
            )
        )
        rows = await cursor.fetchall()
        assert prepared == [PreparedTable(table=table, swap=False)]
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
            (20, "name20"),
            (21, "name21"),
            (22, "name22"),
            (23, "name23"),
            (24, "name24"),
            (25, "name25"),
            (26, "name26"),
            (27, "name27"),
            (28, "name28"),
            (29, "name29"),
        ]

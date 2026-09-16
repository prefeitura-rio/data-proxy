"""Tests for Parquet-to-PostgreSQL loading operations."""

from unittest.mock import AsyncMock, patch

import pytest

from dp.models import (
    PartitionedTable,
    PartitionedTablePlan,
    PartitioningConfig,
    PhysicalPartition,
    RangeSelection,
    SyncPlan,
)
from dp.publication import (
    PreparedTable,
    create_partitioned_table,
    prepare_tables,
)
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, fetch_all, partition, sync_config


class TestLoadingPrepareTablesPartitions:
    """Tests for partitioned table preparation."""

    @pytest.mark.asyncio
    async def test_prepare_tables_incrementally_replaces_affected_partitions(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: an existing partitioned table with data in partitions 10, 20, and 30.
        WHEN: prepare_tables runs incrementally with changed partition 10 and removed partition 20.
        THEN: partition 10 is replaced, partition 20 is deleted, partition 30 is unchanged.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
        )
        changed = partition("10")
        removed = partition("20")
        kept = partition("30")
        path = "/test-files/people_partition_10.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
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

        await execute_sql(
            postgres.connection,
            "postgres/create_people_table",
            mapping={"schema": postgres.namespace.schema},
        )
        await execute_sql(
            postgres.connection,
            "postgres/insert_people_rows",
            mapping={
                "schema": postgres.namespace.schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'old20'), (21, 'old21'), (30, 'keep30'), (31, 'keep31')",
            },
        )
        await postgres.connection.commit()

        prepared = await prepare_tables(
            postgres.connection,
            sync_config([table], schema_name=postgres.namespace.schema),
            plan,
            {table.name},
        )

        remaining = await fetch_all(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": postgres.namespace.schema},
        )

        assert prepared == [PreparedTable(table=table, swap=False)]
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

    @pytest.mark.asyncio
    async def test_prepare_tables_incremental_failure_rolls_back_the_whole_table(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: an existing partitioned table with data in partitions 10, 20, and 30.
        WHEN: prepare_tables runs incrementally but one batch's Parquet path does not exist.
        THEN: the transaction rolls back, so the table keeps its original rows and
        the table is not prepared.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
        )
        changed_10 = partition("10")
        changed_20 = partition("20")
        kept = partition("30")
        path_10 = "/test-files/people_partition_10.parquet"
        path_20_missing = "/test-files/nonexistent.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
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

        await execute_sql(
            postgres.connection,
            "postgres/create_people_table",
            mapping={"schema": postgres.namespace.schema},
        )
        await execute_sql(
            postgres.connection,
            "postgres/insert_people_rows",
            mapping={
                "schema": postgres.namespace.schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'old20'), (21, 'old21'), (30, 'keep30'), (31, 'keep31')",
            },
        )
        await postgres.connection.commit()

        with patch("dp.publication.emit_error", new_callable=AsyncMock):
            prepared = await prepare_tables(
                postgres.connection,
                sync_config([table], schema_name=postgres.namespace.schema),
                plan,
                {table.name},
            )

        remaining = await fetch_all(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": postgres.namespace.schema},
        )

        assert prepared == []
        assert remaining == [
            (10, "old10"),
            (11, "old11"),
            (20, "old20"),
            (21, "old21"),
            (30, "keep30"),
            (31, "keep31"),
        ]

    @pytest.mark.asyncio
    async def test_prepare_tables_incremental_failure_in_a_removed_partition_rolls_back(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: an existing table with a removed partition that references a non-existent column.
        WHEN: prepare_tables runs incrementally.
        THEN: the whole transaction rolls back, so the changed partition keeps its
        original rows and the table is not prepared.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
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
            schema_name=postgres.namespace.schema,
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

        await execute_sql(
            postgres.connection,
            "postgres/create_people_table",
            mapping={"schema": postgres.namespace.schema},
        )
        await execute_sql(
            postgres.connection,
            "postgres/insert_people_rows",
            mapping={
                "schema": postgres.namespace.schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'keep20')",
            },
        )
        await postgres.connection.commit()

        with patch("dp.publication.emit_error", new_callable=AsyncMock):
            prepared = await prepare_tables(
                postgres.connection,
                sync_config([table], schema_name=postgres.namespace.schema),
                plan,
                {table.name},
            )

        remaining = await fetch_all(
            postgres.connection,
            "postgres/select_people_rows",
            mapping={"schema": postgres.namespace.schema},
        )

        assert prepared == []
        assert remaining == [
            (10, "old10"),
            (11, "old11"),
            (20, "keep20"),
        ]

    @pytest.mark.asyncio
    async def test_prepare_tables_full_rebuilds_partitioned_into_a_shadow(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: an existing partitioned table that needs a full rebuild.
        WHEN: prepare_tables runs with two batches.
        THEN: it loads a shadow table from the first batch, appends the second,
        and leaves the live table untouched for the swap.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
        )
        first = "s3://bucket/app/people/batches/0/data.parquet"
        second = "s3://bucket/app/people/batches/1/data.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10"), "20": partition("20")},
                    changed_paths={"10": first, "20": second},
                    removed_partitions={},
                )
            },
        )

        with (
            patch("dp.publication.table_exists", return_value=True),
            patch("dp.publication.column_select_list", return_value="SELECT 1"),
            patch("dp.publication.bootstrap_table"),
            patch("dp.publication.cast_json_columns_to_jsonb"),
            patch("dp.publication.execute_sql", new_callable=AsyncMock),
        ):
            prepared = await prepare_tables(
                postgres.connection,
                sync_config([table]),
                plan,
                {table.name},
            )

        assert prepared == [PreparedTable(table=table, swap=True)]

    @pytest.mark.asyncio
    async def test_prepare_tables_creates_a_missing_partitioned_table_directly(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: a partitioned full rebuild for a table that does not exist.
        WHEN: prepare_tables runs.
        THEN: it creates the live table directly and needs no swap.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
        )
        first = "s3://bucket/app/people/batches/0/data.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": first},
                    removed_partitions={},
                )
            },
        )
        with (
            patch("dp.publication.table_exists", return_value=False),
            patch("dp.publication.column_select_list", return_value="SELECT 1"),
            patch("dp.publication.bootstrap_table"),
            patch("dp.publication.cast_json_columns_to_jsonb"),
            patch("dp.publication.execute_sql", new_callable=AsyncMock),
        ):
            prepared = await prepare_tables(
                postgres.connection,
                sync_config([table]),
                plan,
                {table.name},
            )

        assert prepared == [PreparedTable(table=table, swap=False)]

    @pytest.mark.asyncio
    async def test_prepare_tables_full_rebuild_with_partitioning_uses_replace_partitions(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: an existing partitioned table with pg_partman config and a full rebuild.
        WHEN: prepare_tables runs.
        THEN: it replaces partitions in place (delete all + re-INSERT) and does not swap.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
            n=7,
            partitioning=PartitioningConfig(column="created_at"),
        )
        first = "s3://bucket/app/people/batches/0/data.parquet"
        second = "s3://bucket/app/people/batches/1/data.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={
                        "10": partition("10"),
                        "20": partition("20"),
                    },
                    changed_paths={"10": first, "20": second},
                    removed_partitions={},
                )
            },
        )

        with (
            patch("dp.publication.table_exists", return_value=True),
            patch("dp.publication.column_select_list", return_value="SELECT 1"),
            patch("dp.publication.bootstrap_table"),
            patch("dp.publication.cast_json_columns_to_jsonb"),
            patch("dp.publication.execute_sql", new_callable=AsyncMock),
            patch(
                "dp.publication.AsyncConnection.connect", new_callable=AsyncMock
            ) as mock_connect,
        ):
            mock_connect.return_value.__aenter__ = AsyncMock(
                return_value=postgres.connection
            )
            mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)
            prepared = await prepare_tables(
                postgres.connection,
                sync_config([table]),
                plan,
                {table.name},
            )

        assert prepared == [PreparedTable(table=table, swap=False)]

    @pytest.mark.asyncio
    async def test_prepare_tables_with_partitioning_calls_partman_maintenance(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: an existing partitioned table with pg_partman config and an incremental plan.
        WHEN: prepare_tables runs.
        THEN: it calls run_partman_maintenance before replacing partitions.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
            n=7,
            partitioning=PartitioningConfig(column="created_at"),
        )
        path = "/test-files/people_partition_10.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10"), "30": partition("30")},
                    changed_paths={"10": path},
                    removed_partitions={"20": partition("20")},
                )
            },
        )

        mock_execute = AsyncMock()
        with (
            patch("dp.publication.table_exists", return_value=True),
            patch("dp.publication.column_select_list", return_value="SELECT 1"),
            patch("dp.publication.execute_sql", new=mock_execute),
            patch(
                "dp.publication.AsyncConnection.connect", new_callable=AsyncMock
            ) as mock_connect,
        ):
            mock_connect.return_value.__aenter__ = AsyncMock(
                return_value=postgres.connection
            )
            mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)
            await prepare_tables(
                postgres.connection,
                sync_config([table]),
                plan,
                {table.name},
            )

        calls = [str(call) for call in mock_execute.call_args_list]
        assert any("run_partman_maintenance" in call for call in calls)

    @pytest.mark.asyncio
    async def test_prepare_tables_first_creation_with_partitioning_uses_create(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: a new partitioned table with pg_partman config that does not exist yet.
        WHEN: prepare_tables runs.
        THEN: it uses the CREATE route and calls create_partitioned_table.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
            n=7,
            partitioning=PartitioningConfig(column="created_at"),
        )
        first = "s3://bucket/app/people/batches/0/data.parquet"
        plan = SyncPlan(
            schema_name=postgres.namespace.schema,
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": first},
                    removed_partitions={},
                )
            },
        )

        with (
            patch("dp.publication.table_exists", return_value=False),
            patch(
                "dp.publication.create_partitioned_table", new_callable=AsyncMock
            ) as mock_create,
            patch(
                "dp.publication.rebuild_table", new_callable=AsyncMock
            ) as mock_rebuild,
        ):
            prepared = await prepare_tables(
                postgres.connection,
                sync_config([table]),
                plan,
                {table.name},
            )

        assert prepared == [PreparedTable(table=table, swap=False)]
        mock_create.assert_called_once()
        mock_rebuild.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_partitioned_table_builds_parent_and_loads_data(
        self,
        postgres: Postgres,
    ) -> None:
        """
        GIVEN: a partitioned table with pg_partman config and Parquet paths.
        WHEN: create_partitioned_table runs.
        THEN: it creates a temp table, creates the partitioned parent, registers
        with pg_partman, bootstraps RLS, and loads data via insert_partition.
        """
        table = PartitionedTable(
            name=f"p.{postgres.namespace.schema}.people",
            resolved_schema=postgres.namespace.schema,
            n=7,
            partitioning=PartitioningConfig(column="created_at"),
        )
        partitioning = PartitioningConfig(column="created_at")
        path = "s3://bucket/app/people/data.parquet"

        with (
            patch("dp.publication.create_table_from_parquet", new_callable=AsyncMock),
            patch(
                "dp.publication.AsyncConnection.connect", new_callable=AsyncMock
            ) as mock_connect,
            patch("dp.publication.execute_sql", new_callable=AsyncMock),
            patch("dp.publication.bootstrap_table", new_callable=AsyncMock),
            patch("dp.publication.column_select_list", return_value="SELECT 1"),
            patch("dp.publication.insert_partition", new_callable=AsyncMock),
            patch("dp.publication.cast_json_columns_to_jsonb", new_callable=AsyncMock),
            patch(
                "dp.publication.create_indexes", new_callable=AsyncMock
            ) as mock_indexes,
        ):
            partman_connection = AsyncMock()
            partman_connection.__aenter__.return_value = postgres.connection
            mock_connect.return_value = partman_connection

            await create_partitioned_table(
                postgres.connection,
                sync_config([table], schema_name=postgres.namespace.schema),
                table,
                partitioning,
                [path],
            )

        mock_indexes.assert_called_once()

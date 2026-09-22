"""Freshness edge coverage."""

from unittest.mock import AsyncMock, call, patch

import pytest
from whenever import Instant

from dp.freshness import (
    delete_partition_freshness,
    record_freshness_failures,
    update_published_freshness,
    upsert_freshness,
)
from dp.models import FullTable, PartitionedTable, PartitionedTablePlan, SyncPlan
from tests.fixtures.types import Postgres
from tests.helpers import fetch_all, fetch_one, partition


@pytest.fixture
def full_table(freshness_tables: tuple[FullTable, PartitionedTable, str]) -> FullTable:
    return freshness_tables[0]


@pytest.fixture
def partitioned_table(
    freshness_tables: tuple[FullTable, PartitionedTable, str],
) -> PartitionedTable:
    return freshness_tables[1]


class TestFreshnessPublishedFreshness:
    """Tests for PublishedFreshness behavior."""

    @pytest.mark.asyncio
    async def test_update_published_freshness_replaces_full_table_rows(
        self,
        postgres: Postgres,
        freshness_tables: tuple[FullTable, PartitionedTable, str],
    ) -> None:
        """
        GIVEN: a full table with existing freshness rows.
        WHEN: update_published_freshness is called for a full publication.
        THEN: all existing rows are replaced with a single success row.
        """
        full_table, _, schema = freshness_tables
        attempted_at = Instant.now()

        await upsert_freshness(
            postgres.connection, full_table, {"old"}, attempted_at, success=True
        )

        await update_published_freshness(
            postgres.connection,
            full_table,
            SyncPlan(
                schema_name=schema,
                signatures={full_table.name: "signature"},
                paths={full_table.name: ["s3://b/t"]},
            ),
            set(),
            attempted_at,
        )
        await postgres.connection.commit()

        rows = await fetch_all(
            postgres.connection,
            "postgres/freshness_partitions_by_table",
            mapping={"schema": schema},
            params=("full",),
        )
        assert rows == [(None, "success")]

    @pytest.mark.asyncio
    async def test_update_published_freshness_records_partition_results(
        self,
        postgres: Postgres,
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a partitioned table with successful, failed, and removed partitions.
        WHEN: update_published_freshness is called.
        THEN: each partition result is recorded with its correct status.
        """
        first = partition("1", "signature-1", column="id", width=1)
        second = partition("2", "signature-2", column="id", width=1)
        removed = partition("3", "signature-3", column="id", width=1)
        plan = SyncPlan(
            schema_name=partitioned_table.resolved_schema,
            partitioned_tables={
                partitioned_table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"1": first, "2": second},
                    changed_paths={"1": "s3://b/1", "2": "s3://b/2"},
                    removed_partitions={"3": removed},
                )
            },
        )
        attempted_at = Instant.now()
        await upsert_freshness(
            postgres.connection, partitioned_table, {"3"}, attempted_at, success=True
        )

        await update_published_freshness(
            postgres.connection, partitioned_table, plan, {"2"}, attempted_at
        )

        await postgres.connection.commit()

        rows = await fetch_all(
            postgres.connection,
            "postgres/freshness_partitions_by_table_ordered",
            mapping={"schema": partitioned_table.resolved_schema},
            params=("partitioned",),
        )
        assert rows == [("1", "success"), ("2", "failure")]


class TestFreshness:
    """Tests for freshness module behavior."""

    @pytest.mark.asyncio
    async def test_empty_freshness_batches_leave_no_rows_modified(
        self,
        postgres: Postgres,
        full_table: FullTable,
    ) -> None:
        """
        GIVEN: empty freshness batches.
        WHEN: upsert_freshness and delete_freshness are called.
        THEN: no rows are modified.
        """
        attempted_at = Instant.now()

        await upsert_freshness(
            postgres.connection, full_table, set(), attempted_at, success=True
        )
        await delete_partition_freshness(postgres.connection, full_table, set())

        await postgres.connection.commit()

        assert await fetch_one(postgres.connection, "postgres/select_one") == (1,)

    @pytest.mark.asyncio
    async def test_record_freshness_failures_uses_explicit_or_changed_partitions(
        self,
        postgres: Postgres,
        freshness_tables: tuple[FullTable, PartitionedTable, str],
    ) -> None:
        """
        GIVEN: a full table and a partitioned table with explicit and plan-derived failures.
        WHEN: record_freshness_failures is called.
        THEN: failure records use the explicit partitions or the plan's changed partitions.
        """
        full, partitioned, schema = freshness_tables
        plan = SyncPlan(
            schema_name=schema,
            partitioned_tables={
                partitioned.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={
                        "1": partition("1", "signature-1", column="id", width=1)
                    },
                    changed_paths={"1": "s3://b/1"},
                    removed_partitions={},
                )
            },
        )

        await record_freshness_failures(
            postgres.connection,
            [full, partitioned],
            plan,
            Instant.now(),
            {full.name: {"override"}},
        )

        await postgres.connection.commit()

        rows = await fetch_all(
            postgres.connection,
            "postgres/freshness_table_partitions",
            mapping={"schema": schema},
        )
        assert rows == [
            ("full", "override", "failure"),
            ("partitioned", "1", "failure"),
        ]


class TestFreshnessTemplates:
    """Tests for freshness SQL template usage."""

    @pytest.mark.asyncio
    async def test_delete_freshness_removes_specified_partitions(
        self,
        postgres: Postgres,
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a partitioned table with a partition to remove.
        WHEN: delete_freshness is called.
        THEN: the partition is removed using the freshness SQL template.
        """
        await delete_partition_freshness(postgres.connection, partitioned_table, {"10"})

        await postgres.connection.commit()

        row = await fetch_one(
            postgres.connection,
            "postgres/freshness_count",
            mapping={"schema": partitioned_table.resolved_schema},
        )
        assert row == (0,)

    @pytest.mark.asyncio
    async def test_upsert_freshness_writes_failure_status_using_enum_template(
        self,
        postgres: Postgres,
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a partitioned table with a failed partition.
        WHEN: upsert_freshness is called.
        THEN: the freshness write uses the shared status enum template with the failure value.
        """
        attempted_at = Instant.now()

        await upsert_freshness(
            postgres.connection,
            partitioned_table,
            {"10"},
            attempted_at,
            success=False,
        )

        await postgres.connection.commit()

        row = await fetch_one(
            postgres.connection,
            "postgres/freshness_status",
            mapping={"schema": partitioned_table.resolved_schema},
        )
        assert row == ("failure",)

    @pytest.mark.asyncio
    async def test_full_rebuild_freshness_resets_to_current_manifest(
        self,
        postgres: Postgres,
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a full rebuild plan with current partitions.
        WHEN: update_published_freshness is called.
        THEN: freshness is reset to the complete current manifest.
        """
        plan = SyncPlan(
            schema_name=partitioned_table.resolved_schema,
            partitioned_tables={
                partitioned_table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=True,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "successful"},
                    removed_partitions={},
                )
            },
        )
        attempted_at = Instant.now()

        await update_published_freshness(
            postgres.connection, partitioned_table, plan, set(), attempted_at
        )

        await postgres.connection.commit()

        rows = await fetch_all(
            postgres.connection,
            "postgres/freshness_partitions",
            mapping={"schema": partitioned_table.resolved_schema},
        )
        assert rows == [("10", "success")]

    @pytest.mark.asyncio
    async def test_incremental_freshness_records_success_failure_and_removal(
        self,
        postgres: Postgres,
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a partial partition publication with successful, failed, and removed partitions.
        WHEN: update_published_freshness is called.
        THEN: freshness matches each result with its correct status.
        """
        plan = SyncPlan(
            schema_name=partitioned_table.resolved_schema,
            partitioned_tables={
                partitioned_table.name: PartitionedTablePlan(
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
            patch("dp.freshness.upsert_freshness", new_callable=AsyncMock) as upsert,
            patch(
                "dp.freshness.delete_partition_freshness", new_callable=AsyncMock
            ) as delete,
        ):
            await update_published_freshness(
                postgres.connection, partitioned_table, plan, {"20"}, attempted_at
            )

        assert upsert.await_args_list == [
            call(
                postgres.connection,
                partitioned_table,
                {"10"},
                attempted_at,
                success=True,
            ),
            call(
                postgres.connection,
                partitioned_table,
                {"20"},
                attempted_at,
                success=False,
            ),
        ]
        delete.assert_awaited_once_with(postgres.connection, partitioned_table, {"30"})

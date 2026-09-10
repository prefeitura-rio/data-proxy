"""Freshness edge coverage."""

from unittest.mock import call, patch

import pytest
from psycopg import Connection
from whenever import Instant

from dp.freshness import (
    delete_partition_freshness,
    record_table_failures,
    update_published_freshness,
    upsert_freshness,
)
from dp.models import FullTable, PartitionedTable, PartitionedTablePlan, SyncPlan
from tests.helpers import execute_sql, partition


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

    def test_update_published_freshness_replaces_full_table_rows(
        self,
        postgres: Connection[tuple[object, ...]],
        freshness_tables: tuple[FullTable, PartitionedTable, str],
    ) -> None:
        """
        GIVEN: a full table with existing freshness rows.
        WHEN: update_published_freshness is called for a full publication.
        THEN: all existing rows are replaced with a single success row.
        """
        full_table, _, schema = freshness_tables
        attempted_at = Instant.now()

        upsert_freshness(postgres, full_table, {"old"}, attempted_at, success=True)

        update_published_freshness(
            postgres,
            full_table,
            SyncPlan(
                schema_name=schema,
                signatures={full_table.name: "signature"},
                paths={full_table.name: ["s3://b/t"]},
            ),
            set(),
            attempted_at,
        )

        assert execute_sql(
            postgres,
            "postgres/freshness_partitions_by_table",
            mapping={"schema": schema},
            params=("full",),
        ).fetchall() == [(None, "success")]

    def test_update_published_freshness_records_partition_results(
        self,
        postgres: Connection[tuple[object, ...]],
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
        upsert_freshness(postgres, partitioned_table, {"3"}, attempted_at, success=True)

        update_published_freshness(
            postgres, partitioned_table, plan, {"2"}, attempted_at
        )

        assert execute_sql(
            postgres,
            "postgres/freshness_partitions_by_table_ordered",
            mapping={"schema": partitioned_table.resolved_schema},
            params=("partitioned",),
        ).fetchall() == [("1", "success"), ("2", "failure")]


class TestFreshness:
    """Tests for freshness module behavior."""

    def test_empty_freshness_batches_leave_no_rows_modified(
        self,
        postgres: Connection[tuple[object, ...]],
        full_table: FullTable,
    ) -> None:
        """
        GIVEN: empty freshness batches.
        WHEN: upsert_freshness and delete_freshness are called.
        THEN: no rows are modified.
        """
        attempted_at = Instant.now()

        upsert_freshness(postgres, full_table, set(), attempted_at, success=True)
        delete_partition_freshness(postgres, full_table, set())

        assert execute_sql(postgres, "postgres/select_one").fetchone() == (1,)

    def test_record_table_failures_uses_explicit_or_changed_partitions(
        self,
        postgres: Connection[tuple[object, ...]],
        freshness_tables: tuple[FullTable, PartitionedTable, str],
    ) -> None:
        """
        GIVEN: a full table and a partitioned table with explicit and plan-derived failures.
        WHEN: record_table_failures is called.
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

        record_table_failures(
            postgres,
            [full, partitioned],
            plan,
            Instant.now(),
            {full.name: {"override"}},
        )

        assert execute_sql(
            postgres, "postgres/freshness_table_partitions", mapping={"schema": schema}
        ).fetchall() == [
            ("full", "override", "failure"),
            ("partitioned", "1", "failure"),
        ]


class TestFreshnessTemplates:
    """Tests for freshness SQL template usage."""

    def test_delete_freshness_removes_specified_partitions(
        self,
        postgres: Connection[tuple[object, ...]],
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a partitioned table with a partition to remove.
        WHEN: delete_freshness is called.
        THEN: the partition is removed using the freshness SQL template.
        """
        delete_partition_freshness(postgres, partitioned_table, {"10"})

        assert execute_sql(
            postgres,
            "postgres/freshness_count",
            mapping={"schema": partitioned_table.resolved_schema},
        ).fetchone() == (0,)

    def test_upsert_freshness_writes_failure_status_using_enum_template(
        self,
        postgres: Connection[tuple[object, ...]],
        partitioned_table: PartitionedTable,
    ) -> None:
        """
        GIVEN: a partitioned table with a failed partition.
        WHEN: upsert_freshness is called.
        THEN: the freshness write uses the shared status enum template with the failure value.
        """
        attempted_at = Instant.now()

        upsert_freshness(
            postgres, partitioned_table, {"10"}, attempted_at, success=False
        )

        assert execute_sql(
            postgres,
            "postgres/freshness_status",
            mapping={"schema": partitioned_table.resolved_schema},
        ).fetchone() == ("failure",)

    def test_full_rebuild_freshness_resets_to_current_manifest(
        self,
        postgres: Connection[tuple[object, ...]],
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

        update_published_freshness(
            postgres, partitioned_table, plan, set(), attempted_at
        )

        assert execute_sql(
            postgres,
            "postgres/freshness_partitions",
            mapping={"schema": partitioned_table.resolved_schema},
        ).fetchall() == [("10", "success")]

    def test_incremental_freshness_records_success_failure_and_removal(
        self,
        postgres: Connection[tuple[object, ...]],
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
            patch("dp.freshness.upsert_freshness") as upsert,
            patch("dp.freshness.delete_partition_freshness") as delete,
        ):
            update_published_freshness(
                postgres, partitioned_table, plan, {"20"}, attempted_at
            )

        assert upsert.call_args_list == [
            call(postgres, partitioned_table, {"10"}, attempted_at, success=True),
            call(postgres, partitioned_table, {"20"}, attempted_at, success=False),
        ]
        delete.assert_called_once_with(postgres, partitioned_table, {"30"})

"""Freshness edge coverage."""

from typing import LiteralString, cast

from psycopg import Connection
from whenever import Instant

from dp.freshness import (
    delete_freshness,
    record_table_failures,
    update_published_freshness,
    upsert_freshness,
)
from dp.models import FullTable, PartitionedTable, PartitionedTablePlan, SyncPlan
from dp.templates import TemplateSpec, load_template
from tests.constants import FILES
from tests.helpers import partition


class TestFreshnessPublishedFreshness:
    """Tests for PublishedFreshness behavior."""

    def test_update_published_freshness_replaces_full_table_rows(
        self,
        postgres: Connection[tuple[object, ...]],
        full_table: FullTable,
    ) -> None:
        """
        GIVEN: a full table with existing freshness rows.
        WHEN: update_published_freshness is called for a full publication.
        THEN: all existing rows are replaced with a single success row.
        """
        attempted_at = Instant.now()

        upsert_freshness(postgres, full_table, {"old"}, attempted_at, success=True)

        update_published_freshness(
            postgres,
            full_table,
            SyncPlan(
                schema_name="app",
                signatures={"p.app.t": "signature"},
                paths={"p.app.t": ["s3://b/t"]},
            ),
            set(),
            attempted_at,
        )

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(
                        path="postgres/freshness_partitions_by_table", mapping={}
                    ),
                    FILES.parent / "sql",
                ),
            ),
            ("t",),
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
            schema_name="app",
            partitioned_tables={
                "p.app.t": PartitionedTablePlan(
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

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(
                        path="postgres/freshness_partitions_by_table_ordered",
                        mapping={},
                    ),
                    FILES.parent / "sql",
                ),
            ),
            ("t",),
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
        delete_freshness(postgres, full_table, set())

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(path="postgres/select_one", mapping={}),
                    FILES.parent / "sql",
                ),
            )
        ).fetchone() == (1,)

    def test_record_table_failures_uses_explicit_or_changed_partitions(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a full table and a partitioned table with explicit and plan-derived failures.
        WHEN: record_table_failures is called.
        THEN: failure records use the explicit partitions or the plan's changed partitions.
        """
        full = FullTable(name="p.app.full", resolved_schema="app")
        partitioned = PartitionedTable(name="p.app.partitioned", resolved_schema="app")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                "p.app.partitioned": PartitionedTablePlan(
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
            {"p.app.full": {"override"}},
        )

        assert postgres.execute(
            cast(
                LiteralString,
                load_template(
                    TemplateSpec(
                        path="postgres/freshness_table_partitions", mapping={}
                    ),
                    FILES.parent / "sql",
                ),
            )
        ).fetchall() == [
            ("full", "override", "failure"),
            ("partitioned", "1", "failure"),
        ]

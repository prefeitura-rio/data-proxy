"""Tests for Parquet-to-PostgreSQL loading operations."""

from unittest.mock import ANY, AsyncMock, patch

import pytest
from psycopg import AsyncConnection
from psycopg.sql import SQL
from whenever import Instant

from dp.loading import apply_sync_plan
from dp.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    SyncPlan,
)
from dp.publication import (
    PreparedTable,
    prepare_tables,
    publish_prepared_tables,
)
from dp.schema import initialize_schemas
from tests.fixtures.types import Postgres
from tests.helpers import fetch_all, fetch_one, partition, sync_config


class TestLoadingPublishPrepared:
    """Tests for PublishPrepared behavior."""

    @pytest.mark.asyncio
    async def test_publish_prepared_tables_swaps_each_table(
        self,
    ) -> None:
        """
        GIVEN: multiple prepared shadow tables.
        WHEN: publish_prepared_tables runs.
        THEN: each table is atomically published.
        """
        tables: list[FullTable | PartitionedTable] = [
            FullTable(name="p.app.one", resolved_schema="app"),
            FullTable(name="p.app.two", resolved_schema="app"),
        ]

        plan = SyncPlan(
            schema_name="app",
            signatures={table.name: "new" for table in tables},
            paths={table.name: [f"s3://b/{table.table_name}"] for table in tables},
        )
        prepared = [PreparedTable(table=table, swap=True) for table in tables]
        connection = AsyncMock(spec=AsyncConnection)

        with (
            patch("dp.publication.publish_table") as publish,
            patch("dp.freshness.execute_sql", new_callable=AsyncMock),
            patch("dp.publication.emit_error", new_callable=AsyncMock),
        ):
            result = await publish_prepared_tables(
                connection,
                prepared,
                plan,
                {},
                Instant.now(),
            )

        assert publish.call_count == 2
        assert connection.commit.call_count == 2
        connection.rollback.assert_not_called()
        assert result == {"p.app.one", "p.app.two"}

    @pytest.mark.asyncio
    async def test_publish_prepared_tables_skips_the_swap_for_a_replaced_partition(
        self,
    ) -> None:
        """
        GIVEN: a prepared table that needs no swap.
        WHEN: publish_prepared_tables runs.
        THEN: it skips the table swap and only updates freshness.
        """
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": "s3://bucket/10.parquet"},
                    removed_partitions={},
                )
            },
        )

        with (
            patch("dp.publication.publish_table") as publish,
            patch("dp.publication.update_published_freshness") as freshness,
        ):
            result = await publish_prepared_tables(
                AsyncMock(spec=AsyncConnection),
                [PreparedTable(table=table, swap=False)],
                plan,
                {},
                Instant.now(),
            )

        publish.assert_not_called()
        freshness.assert_called_once()
        assert result == {"p.app.people"}

    @pytest.mark.asyncio
    async def test_publish_prepared_tables_excludes_failed_publication(
        self,
    ) -> None:
        """
        GIVEN: one table swap raises RuntimeError.
        WHEN: publish_prepared_tables runs.
        THEN: only the successful tables are reported as synchronized.
        """
        tables = [
            FullTable(name="p.app.one", resolved_schema="app"),
            FullTable(name="p.app.two", resolved_schema="app"),
        ]
        connection = AsyncMock(spec=AsyncConnection)

        plan = SyncPlan(
            schema_name="app",
            signatures={table.name: "new" for table in tables},
            paths={table.name: [f"s3://b/{table.table_name}"] for table in tables},
        )
        with (
            patch(
                "dp.publication.publish_table", side_effect=[RuntimeError("boom"), None]
            ),
            patch("dp.freshness.execute_sql", new_callable=AsyncMock),
            patch("dp.publication.emit_error", new_callable=AsyncMock),
        ):
            result = await publish_prepared_tables(
                connection,
                [PreparedTable(table=table, swap=True) for table in tables],
                plan,
                {"p.app.one": {"10"}},
                Instant.now(),
            )

        assert result == {"p.app.two"}
        assert connection.rollback.call_count == 1
        assert connection.commit.call_count == 3


class TestLoadingApplySyncPlan:
    """Tests for ApplySyncPlan behavior."""

    @pytest.mark.asyncio
    async def test_apply_sync_plan_delegates_all_steps(
        self,
    ) -> None:
        """
        GIVEN: a sync config and plan with changes.
        WHEN: apply_sync_plan runs.
        THEN: the orchestrator delegates to prepare, publish, and reload.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )

        with (
            patch("dp.schema.initialize_schemas") as initialize,
            patch("dp.loading.record_extraction_failures"),
            patch(
                "dp.loading.prepare_tables",
                return_value=[PreparedTable(table=config.tables[0], swap=True)],
            ),
            patch(
                "dp.loading.publish_prepared_tables",
                return_value={"p.app.changed"},
            ) as publish,
            patch("dp.loading.revoke_anonymous_access") as reload,
            patch("dp.loading.create_bq_views"),
            patch("dp.loading.emit_error", new_callable=AsyncMock),
        ):
            result = await apply_sync_plan(
                AsyncMock(spec=AsyncConnection), config, plan
            )

        initialize.assert_not_called()
        publish.assert_called_once()
        reload.assert_called_once()
        assert result.plan == plan
        assert result.published_tables == {"p.app.changed"}

    @pytest.mark.asyncio
    async def test_apply_sync_plan_publishes_silo_parquet(
        self,
        postgres: Postgres,
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
        with patch("dp.loading.create_bq_views"):
            await initialize_schemas(
                postgres.connection,
                sync_config([table], schema_name=postgres.namespace.schema),
            )
            result = await apply_sync_plan(
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
        self,
        postgres: Postgres,
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
        self,
        postgres: Postgres,
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

    @pytest.mark.asyncio
    async def test_apply_sync_plan_does_not_initialize_schemas(
        self,
    ) -> None:
        """
        GIVEN: a publication connection.
        WHEN: apply_sync_plan runs.
        THEN: it never initializes schemas, because schema initialization needs
              a backend without pg_duckdb state and runs as a separate step.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(schema_name="app")

        with (
            patch("dp.schema.initialize_schemas") as initialize,
            patch("dp.loading.record_extraction_failures"),
            patch("dp.loading.prepare_tables", return_value=[]),
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.revoke_anonymous_access"),
            patch("dp.loading.create_bq_views"),
            patch("dp.loading.emit_error", new_callable=AsyncMock),
        ):
            await apply_sync_plan(AsyncMock(spec=AsyncConnection), config, plan)

        initialize.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_sync_plan_creates_fallback_views_when_enabled(
        self,
    ) -> None:
        """Fallback creates BigQuery views after publication."""
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(schema_name="app")
        with (
            patch("dp.schema.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]),
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.revoke_anonymous_access"),
            patch("dp.loading.create_bq_views") as create_views,
            patch("dp.loading.emit_error", new_callable=AsyncMock),
        ):
            conn = AsyncMock(spec=AsyncConnection)
            await apply_sync_plan(conn, config, plan)

        create_views.assert_called_once_with(conn, config)

    @pytest.mark.asyncio
    async def test_apply_sync_plan_records_failure_without_incremental_publication(
        self,
    ) -> None:
        """
        GIVEN: a fully failed incremental change.
        WHEN: apply_sync_plan runs.
        THEN: it records failure without performing a publication swap.
        """
        table = PartitionedTable(name="p.app.people", resolved_schema="app")
        path = "s3://bucket/people/10.parquet"
        plan = SyncPlan(
            schema_name="app",
            partitioned_tables={
                table.name: PartitionedTablePlan(
                    table_signature="table",
                    full_rebuild=False,
                    current_partitions={"10": partition("10")},
                    changed_paths={"10": path},
                    removed_partitions={},
                )
            },
        )

        with (
            patch("dp.schema.initialize_schemas"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.record_table_failures") as record_failures,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.revoke_anonymous_access"),
            patch("dp.loading.create_bq_views"),
            patch("dp.loading.emit_error", new_callable=AsyncMock),
        ):
            result = await apply_sync_plan(
                AsyncMock(spec=AsyncConnection),
                sync_config([table]),
                plan,
                {path},
            )

        prepare.assert_called_once_with(ANY, ANY, ANY, set())
        record_failures.assert_called_once_with(
            ANY, [table], plan, ANY, {table.name: {"10"}}
        )
        assert result.published_tables == set()

    @pytest.mark.asyncio
    async def test_apply_sync_plan_excludes_extraction_failures(
        self,
    ) -> None:
        """
        GIVEN: a table with a failed extraction path.
        WHEN: apply_sync_plan runs.
        THEN: the table is not prepared from stale Parquet.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )

        with (
            patch("dp.schema.initialize_schemas"),
            patch("dp.loading.record_extraction_failures"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.revoke_anonymous_access"),
            patch("dp.loading.create_bq_views"),
            patch("dp.loading.emit_error", new_callable=AsyncMock),
        ):
            result = await apply_sync_plan(
                AsyncMock(spec=AsyncConnection),
                config,
                plan,
                {"s3://bucket/changed/data.parquet"},
            )

        prepare.assert_called_once_with(ANY, config, plan, set())
        assert result.plan == plan
        assert result.published_tables == set()

    @pytest.mark.asyncio
    async def test_apply_sync_plan_records_preparation_failure_for_eligible_table(
        self,
    ) -> None:
        """
        GIVEN: an eligible table that fails to prepare.
        WHEN: apply_sync_plan runs.
        THEN: it records the preparation failure without publishing.
        """
        config = sync_config([FullTable(name="p.app.changed")])
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.changed": "100"},
            paths={"p.app.changed": ["s3://bucket/changed/data.parquet"]},
        )

        with (
            patch("dp.schema.initialize_schemas"),
            patch("dp.loading.record_extraction_failures"),
            patch("dp.loading.prepare_tables", return_value=[]) as prepare,
            patch("dp.loading.record_table_failures") as record_failures,
            patch("dp.loading.publish_prepared_tables", return_value=set()),
            patch("dp.loading.revoke_anonymous_access"),
            patch("dp.loading.create_bq_views"),
            patch("dp.loading.emit_error", new_callable=AsyncMock),
        ):
            result = await apply_sync_plan(
                AsyncMock(spec=AsyncConnection), config, plan
            )

        prepare.assert_called_once_with(ANY, ANY, ANY, {"p.app.changed"})
        record_failures.assert_called_with(ANY, [config.tables[0]], plan, ANY)
        assert result.published_tables == set()

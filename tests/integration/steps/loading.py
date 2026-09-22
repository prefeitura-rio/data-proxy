"""Integration steps for Parquet and S3 loading."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from psycopg.sql import SQL
from pytest_bdd import given, then, when

from data_proxy.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
    TableConfig,
)
from data_proxy.publication import PreparedTable, prepare_tables, run_publication
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, fetch_all, fetch_one, partition


@dataclass
class LoadingScenario:
    postgres: Postgres
    table_name: str = "people"


def sync_config_helper(tables: list[TableConfig], schema: str) -> SyncConfig:
    return SyncConfig(schemas={schema: SchemaConfig(tables=tables)})


@given("a fresh PostgreSQL loading schema", target_fixture="loading_context")
def fresh_loading_schema(postgres: Postgres) -> LoadingScenario:
    return LoadingScenario(postgres=postgres)


@when("I incrementally replace partition 10 and remove partition 20")
def incremental_replace_and_remove(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    schema = database.namespace.schema
    table = PartitionedTable(name=f"p.{schema}.people", resolved_schema=schema)
    changed = partition("10")
    removed = partition("20")
    kept = partition("30")
    path = "/test-files/people_partition_10.parquet"
    plan = SyncPlan(
        schema_name=schema,
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
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_people_table",
            mapping={"schema": schema},
        )
    )
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/insert_people_rows",
            mapping={
                "schema": schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'old20'), (21, 'old21'), (30, 'keep30'), (31, 'keep31')",
            },
        )
    )
    asyncio.run(database.connection.commit())
    loading_context.table_name = table.name
    prepared = asyncio.run(
        prepare_tables(
            database.connection,
            database.connection,
            sync_config_helper([table], schema),
            plan,
            {table.name},
        )
    )
    assert prepared == [PreparedTable(table=table, swap=False)]


@when("I load a missing Parquet partition")
def load_missing_parquet(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    schema = database.namespace.schema
    table = PartitionedTable(name=f"p.{schema}.people", resolved_schema=schema)
    changed_10 = partition("10")
    changed_20 = partition("20")
    kept = partition("30")
    path_10 = "/test-files/people_partition_10.parquet"
    path_20_missing = "/test-files/nonexistent.parquet"
    plan = SyncPlan(
        schema_name=schema,
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"10": changed_10, "20": changed_20, "30": kept},
                changed_paths={"10": path_10, "20": path_20_missing},
                removed_partitions={},
            )
        },
    )
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_people_table",
            mapping={"schema": schema},
        )
    )
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/insert_people_rows",
            mapping={
                "schema": schema,
                "rows": "(10, 'old10'), (11, 'old11'), (20, 'old20'), (21, 'old21'), (30, 'keep30'), (31, 'keep31')",
            },
        )
    )
    asyncio.run(database.connection.commit())
    with patch("data_proxy.publication.emit_error", new_callable=AsyncMock):
        prepared = asyncio.run(
            prepare_tables(
                database.connection,
                database.connection,
                sync_config_helper([table], schema),
                plan,
                {table.name},
            )
        )
    assert prepared == []


@when("I create a full table from S3 Parquet")
def create_full_table_from_s3(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    schema = database.namespace.schema
    table = FullTable(
        name=f"p.{schema}.people",
        resolved_schema=schema,
        indexes=[IndexConfig(name="idx_people_cpf", columns=["cpf"])],
    )
    plan = SyncPlan(
        schema_name=schema,
        signatures={table.name: "sig"},
        paths={table.name: [f"s3://test-bucket/{schema}/people/data.parquet"]},
    )
    prepared = asyncio.run(
        prepare_tables(
            database.connection,
            database.connection,
            sync_config_helper([table], schema),
            plan,
            {table.name},
        )
    )
    assert prepared == [PreparedTable(table=table, swap=False)]
    loading_context.table_name = "people"


@when("I rebuild a partitioned table from two S3 batches")
def rebuild_partitioned_from_batches(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    schema = database.namespace.schema
    table = PartitionedTable(name=f"p.{schema}.people", resolved_schema=schema)
    first = f"s3://test-bucket/{schema}/people/data.parquet"
    second = "s3://test-bucket/app/people/people_partition_20.parquet"
    plan = SyncPlan(
        schema_name=schema,
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
    prepared = asyncio.run(
        prepare_tables(
            database.connection,
            database.connection,
            sync_config_helper([table], schema),
            plan,
            {table.name},
        )
    )
    assert prepared == [PreparedTable(table=table, swap=False)]
    loading_context.table_name = "people"


@when("I publish an S3-backed full table")
def publish_s3_backed_table(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    schema = database.namespace.schema
    table = FullTable(name=f"p.{schema}.people", resolved_schema=schema)
    plan = SyncPlan(
        schema_name=schema,
        signatures={table.name: "sig"},
        paths={table.name: [f"s3://test-bucket/{schema}/people/data.parquet"]},
    )
    with patch("data_proxy.publication.run_fallback_views_creation"):
        result = asyncio.run(
            run_publication(
                database.connection,
                database.connection,
                sync_config_helper([table], schema),
                plan,
            )
        )
    assert result.published_tables == {table.name}
    loading_context.table_name = "people"


@then("partition 10 has the new data")
def check_partition_10_new(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    rows = asyncio.run(
        fetch_all(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    expected_10 = [(i, f"name{i}") for i in range(10, 20)]
    assert all(row in rows for row in expected_10)


@then("partition 20 is removed")
def check_partition_20_removed(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    rows = asyncio.run(
        fetch_all(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    assert not any(row[0] == 20 for row in rows)
    assert not any(row[0] == 21 for row in rows)


@then("partition 30 is unchanged")
def check_partition_30_unchanged(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    rows = asyncio.run(
        fetch_all(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    assert (30, "keep30") in rows
    assert (31, "keep31") in rows


@then("the table keeps its original rows")
def check_original_rows(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    rows = asyncio.run(
        fetch_all(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    assert rows == [
        (10, "old10"),
        (11, "old11"),
        (20, "old20"),
        (21, "old21"),
        (30, "keep30"),
        (31, "keep31"),
    ]


@then("the table contains all partition rows")
def check_all_partition_rows(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    rows = asyncio.run(
        fetch_all(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    assert rows == [(i, f"name{i}") for i in range(10, 20)]


@then("the configured index exists on the table")
def check_loading_index(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    rows = asyncio.run(
        fetch_all(
            database.connection,
            "postgres/index_names",
            mapping={
                "schema": database.namespace.schema,
                "table": loading_context.table_name,
            },
        )
    )
    assert rows == [("idx_people_cpf",)]


@then("the table contains rows from both batches")
def check_both_batches(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    cursor = asyncio.run(
        database.connection.execute(
            SQL("SELECT cpf, name FROM {} ORDER BY cpf").format(
                database.namespace.table("people")
            )
        )
    )
    rows = asyncio.run(cursor.fetchall())
    assert rows == [(i, f"name{i}") for i in range(10, 30)]


@then("the published table contains the S3 data")
def check_published_silo_data(loading_context: LoadingScenario) -> None:
    database = loading_context.postgres
    row = asyncio.run(
        fetch_one(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    assert row == (10, "name10")

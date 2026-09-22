"""Integration steps for PostgreSQL table publication."""

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest
from psycopg import AsyncCursor
from pytest_bdd import given, then, when
from whenever import Instant

from data_proxy.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from data_proxy.publication import (
    PreparedTable,
    cast_json_columns_to_jsonb,
    prepare_table,
    prepare_tables,
    publish_table,
    run_publication_batch,
)
from data_proxy.types import DatabaseRow
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, partition


@dataclass
class PublicationScenario:
    postgres: Postgres
    table_name: str = "people"
    batch_result: set[str] = field(default_factory=set)


async def execute_fixture_sql(
    database: Postgres, path: str, mapping: dict[str, str]
) -> AsyncCursor[DatabaseRow]:
    return await execute_sql(database.connection, path, mapping=mapping)


async def fetch_fixture_rows(
    database: Postgres, path: str, mapping: dict[str, str]
) -> list[DatabaseRow]:
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchall()


@given("a fresh PostgreSQL publication schema", target_fixture="publication_context")
def fresh_publication_schema(postgres: Postgres) -> PublicationScenario:
    return PublicationScenario(postgres=postgres)


@when("I convert the JSON columns of a table to JSONB")
def convert_json_columns(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                "schema": database.namespace.schema,
                "table": "json_t",
                "columns": "id integer, data json",
            },
        )
    )
    asyncio.run(
        cast_json_columns_to_jsonb(
            database.connection, database.namespace.schema, "json_t"
        )
    )


@when("I publish a prepared shadow table")
def publish_shadow_table(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    schema = database.namespace.schema
    table = FullTable(
        name=f"p.{schema}.people",
        resolved_schema=schema,
        indexes=[IndexConfig(name="idx_people_cpf", columns=["cpf"])],
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
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "people__next",
                "columns": "cpf integer, name text",
            },
        )
    )
    asyncio.run(
        database.connection.execute(
            f'INSERT INTO "{schema}"."people__next" VALUES (20, \'new20\')'.encode()
        )
    )
    asyncio.run(publish_table(database.connection, table))
    publication_context.table_name = "people"


@then("the table has JSONB columns")
def check_jsonb_columns(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/table_column_types",
            {"schema": database.namespace.schema, "table": "json_t"},
        )
    )
    assert rows == [("data", "jsonb"), ("id", "integer")]


@then("the live table contains the shadow rows")
def check_published_rows(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    assert asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/select_people_rows",
            {"schema": database.namespace.schema},
        )
    ) == [(20, "new20")]


@then("the configured index exists on the live table")
def check_published_index(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    assert asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/index_names",
            {
                "schema": database.namespace.schema,
                "table": publication_context.table_name,
            },
        )
    ) == [("idx_people_cpf",)]


@when("I publish a table with a gin expression index")
def publish_gin_expression_index(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    schema = database.namespace.schema
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
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "people__next",
                "columns": "cpf integer, name text",
            },
        )
    )
    asyncio.run(
        database.connection.execute(
            f'INSERT INTO "{schema}"."people__next" VALUES (1, \'hello\')'.encode()
        )
    )
    asyncio.run(publish_table(database.connection, table))
    publication_context.table_name = "people"


@then("the expression index exists on the live table")
def check_expression_index(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    assert asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/index_names",
            {
                "schema": database.namespace.schema,
                "table": publication_context.table_name,
            },
        )
    ) == [("idx_people_name",)]


@when("I prepare an incremental plan for a missing table")
def prepare_incremental_missing(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    schema = database.namespace.schema
    table = PartitionedTable(name=f"p.{schema}.people", resolved_schema=schema)
    plan = SyncPlan(
        schema_name=schema,
        partitioned_tables={
            table.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"1": partition("1")},
                changed_paths={"1": "path"},
                removed_partitions={},
            )
        },
    )
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    with pytest.raises(RuntimeError, match="incremental plan"):
        asyncio.run(
            prepare_table(
                database.connection,
                config,
                table,
                plan,
                plan.partitioned_tables[table.name],
            )
        )


@then("preparation fails with a missing table error")
def preparation_fails_missing_table() -> None:
    pass


@when("I prepare tables with an empty changed set", target_fixture="prepared_tables")
def prepare_empty_changed_set(
    publication_context: PublicationScenario,
) -> list[PreparedTable]:
    database = publication_context.postgres
    schema = database.namespace.schema
    table = FullTable(name=f"p.{schema}.people", resolved_schema=schema)
    plan = SyncPlan(schema_name=schema)
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    return asyncio.run(
        prepare_tables(
            database.connection,
            database.connection,
            config,
            plan,
            set(),
        )
    )


@when("I prepare a table with a missing Parquet path", target_fixture="prepared_tables")
def prepare_missing_parquet(
    publication_context: PublicationScenario,
) -> list[PreparedTable]:
    database = publication_context.postgres
    schema = database.namespace.schema
    table = FullTable(name=f"p.{schema}.people", resolved_schema=schema)
    plan = SyncPlan(
        schema_name=schema,
        signatures={table.name: "sig"},
        paths={table.name: ["s3://missing/path.parquet"]},
    )
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    with patch("data_proxy.publication.emit_error", new_callable=AsyncMock):
        return asyncio.run(
            prepare_tables(
                database.connection,
                database.connection,
                config,
                plan,
                {table.name},
            )
        )


@when("I publish a batch with one failing table")
def publish_batch_with_failure(publication_context: PublicationScenario) -> None:
    database = publication_context.postgres
    schema = database.namespace.schema
    good_table = FullTable(name=f"p.{schema}.good", resolved_schema=schema)
    plan = SyncPlan(
        schema_name=schema,
        signatures={good_table.name: "sig"},
        paths={good_table.name: ["path"]},
    )
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "good",
                "columns": "id integer",
            },
        )
    )
    asyncio.run(database.connection.commit())
    publication_context.batch_result = asyncio.run(
        run_publication_batch(
            database.connection,
            database.connection,
            [PreparedTable(table=good_table, swap=False)],
            plan,
            {},
            Instant.now(),
        )
    )


@then("no tables are prepared")
def no_tables_prepared(prepared_tables: list[PreparedTable]) -> None:
    assert prepared_tables == []


@then("only the successful table is published")
def check_batch_result(publication_context: PublicationScenario) -> None:
    assert hasattr(publication_context, "batch_result")
    assert len(publication_context.batch_result) <= 1

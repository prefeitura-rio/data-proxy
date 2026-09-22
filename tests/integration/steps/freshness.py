"""Integration steps for publication freshness."""

import asyncio
from dataclasses import dataclass

from pytest_bdd import given, then, when
from whenever import Instant

from data_proxy.freshness import (
    delete_partition_freshness,
    record_freshness_failures,
    update_published_freshness,
    upsert_freshness,
)
from data_proxy.models import (
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    SyncPlan,
)
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, fetch_all, fetch_one, partition


@dataclass
class FreshnessScenario:
    postgres: Postgres
    full: FullTable
    partitioned: PartitionedTable
    schema: str


@given("initialized freshness tables", target_fixture="freshness_context")
def initialized_freshness_tables(postgres: Postgres) -> FreshnessScenario:
    schema = postgres.namespace.schema
    full = FullTable(name=f"p.{schema}.full", resolved_schema=schema)
    partitioned = PartitionedTable(
        name=f"p.{schema}.partitioned", resolved_schema=schema
    )
    asyncio.run(
        execute_sql(
            postgres.connection,
            "postgres/create_freshness_table",
            mapping={"schema": schema},
        )
    )
    return FreshnessScenario(postgres, full, partitioned, schema)


@when("I publish a full-table freshness result")
def publish_full_freshness(freshness_context: FreshnessScenario) -> None:
    context = freshness_context
    attempted = Instant.now()
    asyncio.run(
        upsert_freshness(
            context.postgres.connection, context.full, {"old"}, attempted, success=True
        )
    )
    plan = SyncPlan(
        schema_name=context.schema,
        signatures={context.full.name: "signature"},
        paths={context.full.name: ["s3://bucket/full"]},
    )
    asyncio.run(
        update_published_freshness(
            context.postgres.connection, context.full, plan, set(), attempted
        )
    )


@when('I publish successful partition "1" and failed partition "2"')
def publish_partition_freshness(freshness_context: FreshnessScenario) -> None:
    context = freshness_context
    first = partition("1", "signature-1", column="id", width=1)
    second = partition("2", "signature-2", column="id", width=1)
    plan = SyncPlan(
        schema_name=context.schema,
        partitioned_tables={
            context.partitioned.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"1": first, "2": second},
                changed_paths={"1": "path-1", "2": "path-2"},
                removed_partitions={},
            )
        },
    )
    asyncio.run(
        update_published_freshness(
            context.postgres.connection, context.partitioned, plan, {"2"}, Instant.now()
        )
    )


@when("I apply empty freshness batches")
def apply_empty_freshness(freshness_context: FreshnessScenario) -> None:
    context = freshness_context
    attempted = Instant.now()
    asyncio.run(
        upsert_freshness(
            context.postgres.connection, context.full, set(), attempted, success=True
        )
    )
    asyncio.run(
        delete_partition_freshness(context.postgres.connection, context.full, set())
    )


@when("I record explicit full-table and derived partition failures")
def record_freshness_failures_step(freshness_context: FreshnessScenario) -> None:
    context = freshness_context
    plan = SyncPlan(
        schema_name=context.schema,
        partitioned_tables={
            context.partitioned.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=False,
                current_partitions={"1": partition("1", column="id", width=1)},
                changed_paths={"1": "path-1"},
                removed_partitions={},
            )
        },
    )
    asyncio.run(
        record_freshness_failures(
            context.postgres.connection,
            [context.full, context.partitioned],
            plan,
            Instant.now(),
            {context.full.name: {"override"}},
        )
    )


@when("I publish a full partition rebuild")
def publish_full_partition_rebuild(freshness_context: FreshnessScenario) -> None:
    context = freshness_context
    plan = SyncPlan(
        schema_name=context.schema,
        partitioned_tables={
            context.partitioned.name: PartitionedTablePlan(
                table_signature="table",
                full_rebuild=True,
                current_partitions={"10": partition("10")},
                changed_paths={"10": "path-10"},
                removed_partitions={},
            )
        },
    )
    asyncio.run(
        update_published_freshness(
            context.postgres.connection,
            context.partitioned,
            plan,
            set(),
            Instant.now(),
        )
    )


@then("the full table has one successful freshness row")
def check_full_freshness(freshness_context: FreshnessScenario) -> None:
    rows = asyncio.run(
        fetch_all(
            freshness_context.postgres.connection,
            "postgres/freshness_partitions_by_table",
            mapping={"schema": freshness_context.schema},
            params=("full",),
        )
    )
    assert rows == [(None, "success")]


@then("partition freshness reports the expected statuses")
def check_partition_freshness(freshness_context: FreshnessScenario) -> None:
    rows = asyncio.run(
        fetch_all(
            freshness_context.postgres.connection,
            "postgres/freshness_partitions_by_table_ordered",
            mapping={"schema": freshness_context.schema},
            params=("partitioned",),
        )
    )
    assert rows == [("1", "success"), ("2", "failure")]


@then("no freshness rows are created")
def check_empty_freshness(freshness_context: FreshnessScenario) -> None:
    assert asyncio.run(
        fetch_one(freshness_context.postgres.connection, "postgres/select_one")
    ) == (1,)


@then("both freshness failures are stored")
def check_freshness_failures(freshness_context: FreshnessScenario) -> None:
    rows = asyncio.run(
        fetch_all(
            freshness_context.postgres.connection,
            "postgres/freshness_table_partitions",
            mapping={"schema": freshness_context.schema},
        )
    )
    assert rows == [
        ("full", "override", "failure"),
        ("partitioned", "1", "failure"),
    ]


@then("the current partition is marked successful")
def check_full_rebuild_freshness(freshness_context: FreshnessScenario) -> None:
    rows = asyncio.run(
        fetch_all(
            freshness_context.postgres.connection,
            "postgres/freshness_partitions",
            mapping={"schema": freshness_context.schema},
        )
    )
    assert rows == [("10", "success")]


@when("I record explicit failure overrides for the full table")
def record_explicit_overrides(freshness_context: FreshnessScenario) -> None:
    context = freshness_context
    asyncio.run(
        record_freshness_failures(
            context.postgres.connection,
            [context.full],
            SyncPlan(schema_name=context.schema),
            Instant.now(),
            {context.full.name: {"override"}},
        )
    )


@then("the explicit override partition is marked failed")
def check_explicit_override(freshness_context: FreshnessScenario) -> None:
    rows = asyncio.run(
        fetch_all(
            freshness_context.postgres.connection,
            "postgres/freshness_table_partitions",
            mapping={"schema": freshness_context.schema},
        )
    )
    assert ("full", "override", "failure") in rows

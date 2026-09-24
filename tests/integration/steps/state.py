"""Integration steps for persisted synchronization state."""

import asyncio

import pytest
from psycopg import AsyncConnection
from pytest_bdd import given, parsers, then, when

from data_proxy.models import (
    FullTable,
    PublicationResult,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableState,
)
from data_proxy.state import (
    build_table_states,
    emit_error,
    read_partition_manifest,
    read_table_signature,
    read_table_state,
    write_table_states,
)
from tests.helpers import partition


@pytest.fixture
def written_table() -> str | None:
    return None


@given("an empty application state database", target_fixture="state_connection")
def empty_state_database(dbos_conn: AsyncConnection) -> AsyncConnection:
    return dbos_conn


@when(
    parsers.parse(
        'I write full-table state for "{table}" with signature "{signature}"'
    ),
    target_fixture="written_table",
)
def write_full_table_state(
    state_connection: AsyncConnection,
    table: str,
    signature: str,
) -> str:
    asyncio.run(
        write_table_states(
            state_connection,
            {table: TableState(strategy=Strategy.FULL, signature=signature)},
        )
    )
    return table


@when(parsers.parse('I replace table "{table}" signature "{old}" with "{new}"'))
def replace_table_state(
    state_connection: AsyncConnection,
    table: str,
    old: str,
    new: str,
) -> None:
    asyncio.run(
        write_table_states(
            state_connection,
            {table: TableState(strategy=Strategy.FULL, signature=old)},
        )
    )
    asyncio.run(
        write_table_states(
            state_connection,
            {table: TableState(strategy=Strategy.FULL, signature=new)},
        )
    )


@when(parsers.parse('I record an extraction error for "{table}"'))
def record_extraction_error(
    state_connection: AsyncConnection,
    table: str,
) -> None:
    asyncio.run(
        emit_error(
            state_connection,
            "extraction_failed",
            table=table,
            error="boom",
        )
    )


@when(parsers.parse('I write stale state for "{table}" and clean unconfigured state'))
@then(parsers.parse('reading table "{table}" returns signature "{signature}"'))
def read_written_signature(
    state_connection: AsyncConnection,
    table: str,
    signature: str,
    written_table: str | None = None,
) -> None:
    if written_table is not None:
        assert written_table == table
    assert asyncio.run(read_table_signature(state_connection, table)) == signature


@then(parsers.parse('reading table "{table}" has no state'))
def read_missing_state(
    state_connection: AsyncConnection,
    table: str,
) -> None:
    assert asyncio.run(read_table_state(state_connection, table)) is None


@then(parsers.parse('the latest error for "{table}" has reason "{reason}"'))
def read_latest_error(
    state_connection: AsyncConnection,
    table: str,
    reason: str,
) -> None:
    cursor = asyncio.run(
        state_connection.execute(
            b"SELECT reason, fields FROM data_proxy.errors ORDER BY id DESC LIMIT 1"
        )
    )
    result = asyncio.run(cursor.fetchone())
    assert result is not None
    assert result[0] == reason
    assert result[1]["table"] == table


@when(
    parsers.parse('I write partitioned state for "{table}" with one partition'),
    target_fixture="partitioned_table",
)
def write_partitioned_state(
    state_connection: AsyncConnection,
    table: str,
) -> str:
    physical = partition("1")
    state = TableState(
        strategy=Strategy.PARTITIONED,
        signature="sig",
        partitions={"1": physical},
    )
    asyncio.run(write_table_states(state_connection, {table: state}))
    return table


@then(
    parsers.parse('reading the partition manifest for "{table}" returns the partition')
)
def check_partition_manifest(
    state_connection: AsyncConnection,
    table: str,
    partitioned_table: str,
) -> None:
    assert partitioned_table == table
    manifest = asyncio.run(read_partition_manifest(state_connection, table))
    assert manifest is not None
    assert "1" in manifest.partitions


@when(
    parsers.parse('I write state for "{published}" and "{unpublished}"'),
    target_fixture="unpublished_table",
)
def write_two_states(
    state_connection: AsyncConnection,
    published: str,
    unpublished: str,
) -> str:
    asyncio.run(
        write_table_states(
            state_connection,
            {
                published: TableState(strategy=Strategy.FULL, signature="pub"),
                unpublished: TableState(strategy=Strategy.FULL, signature="unpub"),
            },
        )
    )
    return unpublished


@when(
    parsers.parse('I build table states for only "{published}"'),
)
def build_states_for_published(
    state_connection: AsyncConnection,
    published: str,
) -> None:
    schema = published.split(".")[1]
    table = FullTable(name=published, resolved_schema=schema)
    config = SyncConfig(schemas={schema: SchemaConfig(tables=[table])})
    plan = SyncPlan(
        schema_name=schema,
        signatures={published: "pub"},
        paths={published: ["path"]},
    )
    result = PublicationResult(plan=plan, published_tables={published})
    states = build_table_states(result, config)
    asyncio.run(write_table_states(state_connection, states))


@then(parsers.parse('state for "{unpublished}" keeps its original signature'))
def check_unpublished_state(
    state_connection: AsyncConnection,
    unpublished: str,
    unpublished_table: str,
) -> None:
    assert unpublished_table == unpublished
    assert asyncio.run(read_table_signature(state_connection, unpublished)) == "unpub"

import asyncio
from dataclasses import dataclass

import pytest
from psycopg import AsyncConnection, AsyncCursor
from pytest_bdd import given, parsers, then, when
from whenever import Instant

from data_proxy.authorization import apply_table_authorization
from data_proxy.models import (
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableConfig,
    TableState,
    UnitMapping,
)
from data_proxy.publication import PreparedTable
from data_proxy.schema import initialize_schemas, revoke_anonymous_access
from data_proxy.state import (
    emit_error,
    read_table_signature,
    read_table_state,
    write_table_states,
)
from data_proxy.types import DatabaseRow
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, fetch_all, fetch_one, partition


@dataclass
class SchemaScenario:
    """State shared by one schema lifecycle scenario."""

    postgres: Postgres
    other_schema: str | None = None
    memberships_unchanged: bool = False


@dataclass
class PublicationScenario:
    """State shared by one publication scenario."""

    postgres: Postgres
    table_name: str = "people"


@dataclass
class FreshnessScenario:
    """State shared by one freshness scenario."""

    postgres: Postgres
    full: FullTable
    partitioned: PartitionedTable
    schema: str


@dataclass
class AuthorizationScenario:
    """State shared by one authorization scenario."""

    postgres: Postgres
    table: str = "table"


@pytest.fixture
def written_table() -> str | None:
    """Provide an optional written-table context value."""
    return None


# --- State scenarios ---


@given("an empty application state database", target_fixture="state_connection")
def empty_state_database(dbos_conn: AsyncConnection) -> AsyncConnection:
    """Provide the isolated empty application state database."""
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
    """Write one full-table state record."""
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
    """Replace one table state signature."""
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
    """Persist one extraction error for a table."""
    asyncio.run(
        emit_error(
            state_connection,
            "extraction_failed",
            table=table,
            error="boom",
        )
    )


@when(parsers.parse('I write stale state for "{table}" and clean unconfigured state'))
def write_and_clean_stale_state(
    state_connection: AsyncConnection,
    table: str,
) -> None:
    """Write stale state and remove unconfigured state."""
    asyncio.run(
        write_table_states(
            state_connection,
            {table: TableState(strategy=Strategy.FULL, signature="stale")},
        )
    )
    asyncio.run(
        state_connection.execute(
            b"CALL data_proxy.cleanup_table_state(%s::jsonb)",
            ('{"schemas": {}}',),
        )
    )


@then(parsers.parse('reading table "{table}" returns signature "{signature}"'))
def read_written_signature(
    state_connection: AsyncConnection,
    table: str,
    signature: str,
    written_table: str | None = None,
) -> None:
    """Assert that the stored table signature can be read back."""
    if written_table is not None:
        assert written_table == table
    assert asyncio.run(read_table_signature(state_connection, table)) == signature


@then(parsers.parse('reading table "{table}" has no state'))
def read_missing_state(
    state_connection: AsyncConnection,
    table: str,
) -> None:
    """Assert that no state exists for a table."""
    assert asyncio.run(read_table_state(state_connection, table)) is None


@then(parsers.parse('the latest error for "{table}" has reason "{reason}"'))
def read_latest_error(
    state_connection: AsyncConnection,
    table: str,
    reason: str,
) -> None:
    """Assert that the latest error has the expected reason and table."""
    cursor = asyncio.run(
        state_connection.execute(
            b"SELECT reason, fields FROM data_proxy.errors ORDER BY id DESC LIMIT 1"
        )
    )
    result = asyncio.run(cursor.fetchone())
    assert result is not None
    assert result[0] == reason
    assert result[1]["table"] == table


# --- Schema scenarios ---


@given("a fresh PostgreSQL schema", target_fixture="schema_context")
def fresh_postgres_schema(postgres: Postgres) -> SchemaScenario:
    """Provide the isolated PostgreSQL integration fixture."""
    return SchemaScenario(postgres=postgres)


@when("I initialize two configured application schemas")
def initialize_two_schemas(schema_context: SchemaScenario) -> None:
    """Initialize two schemas through the application schema lifecycle."""
    database = schema_context.postgres
    schema = database.namespace.schema
    other = f"{schema}_two"
    config = SyncConfig(
        schemas={
            schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")]),
            other: SchemaConfig(tables=[FullTable(name=f"p.{other}.two")]),
        }
    )
    asyncio.run(initialize_schemas(database.connection, config))
    schema_context.other_schema = other


@when("I initialize one configured application schema")
def initialize_one_schema(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    before = asyncio.run(
        fetch_fixture_rows(
            database, "postgres/role_memberships", {"role": "authenticator"}
        )
    )
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")])}
    )
    asyncio.run(initialize_schemas(database.connection, config))
    after = asyncio.run(
        fetch_fixture_rows(
            database, "postgres/role_memberships", {"role": "authenticator"}
        )
    )
    schema_context.memberships_unchanged = before == after


@when("I revoke anonymous access for the configured schema")
def revoke_schema_access(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")])}
    )
    asyncio.run(
        execute_fixture_sql(
            database,
            "postgres/setup_schema_reload_privileges",
            {"schema": schema},
        )
    )
    asyncio.run(revoke_anonymous_access(database.connection, config))


@when("I create a stale table and clean schema objects")
def clean_schema_objects(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.kept")])}
    )
    asyncio.run(initialize_schemas(database.connection, config))
    asyncio.run(
        database.connection.execute(f'CREATE TABLE "{schema}".stale (id int)'.encode())
    )
    asyncio.run(
        database.connection.execute(
            b"CALL data_proxy.cleanup_stale_objects(%s::jsonb, %s)",
            (config.model_dump_json(), schema),
        )
    )


@then("the maintenance procedures are installed")
def check_maintenance_procedures(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    cursor = asyncio.run(
        database.connection.execute(
            "SELECT to_regprocedure('data_proxy.apply_retention(jsonb,text)')"
        )
    )
    assert asyncio.run(cursor.fetchone()) == ("data_proxy.apply_retention(jsonb,text)",)


@then("both configured schemas exist")
def check_configured_schemas(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    schema = database.namespace.schema
    assert schema_context.other_schema is not None
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/schema_names",
            {"schema": schema, "other_schema": schema_context.other_schema},
        )
    )
    assert rows == [(schema,), (schema_context.other_schema,)]


@then("authenticator memberships are unchanged")
def check_memberships(schema_context: SchemaScenario) -> None:
    assert schema_context.memberships_unchanged


@then("anonymous schema usage is disabled")
def check_schema_usage(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    assert asyncio.run(
        fetch_fixture_one(
            database,
            "postgres/has_schema_usage",
            {"schema": database.namespace.schema},
        )
    ) == (False,)


@then("anonymous table access is disabled")
def check_table_access(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    assert asyncio.run(
        fetch_fixture_one(
            database,
            "postgres/has_table_select",
            {"schema": database.namespace.schema},
        )
    ) == (False,)


@then("the stale table does not exist")
def check_stale_table_removed(schema_context: SchemaScenario) -> None:
    database = schema_context.postgres
    cursor = asyncio.run(
        database.connection.execute(
            b"SELECT to_regclass(%s)",
            (f"{database.namespace.schema}.stale",),
        )
    )
    assert asyncio.run(cursor.fetchone()) == (None,)


# --- Publication scenarios ---


@given("a fresh PostgreSQL publication schema", target_fixture="publication_context")
def fresh_publication_schema(postgres: Postgres) -> PublicationScenario:
    """Provide an isolated PostgreSQL publication schema."""
    return PublicationScenario(postgres=postgres)


@when("I convert the JSON columns of a table to JSONB")
def convert_json_columns(publication_context: PublicationScenario) -> None:
    from data_proxy.publication import cast_json_columns_to_jsonb

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
    from data_proxy.publication import publish_table

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


# --- Freshness scenarios ---


@given("initialized freshness tables", target_fixture="freshness_context")
def initialized_freshness_tables(postgres: Postgres) -> FreshnessScenario:
    """Provide initialized freshness tables in PostgreSQL."""
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
    from data_proxy.freshness import update_published_freshness, upsert_freshness

    attempted = Instant.now()
    context = freshness_context
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
    from data_proxy.freshness import update_published_freshness

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
    from data_proxy.freshness import delete_partition_freshness, upsert_freshness

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
    from data_proxy.freshness import record_freshness_failures

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
    from data_proxy.freshness import update_published_freshness

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


# --- Authorization scenarios ---


@given(
    "a fresh PostgreSQL authorization schema",
    target_fixture="authorization_context",
)
def fresh_authorization_schema(postgres: Postgres) -> AuthorizationScenario:
    """Provide one isolated PostgreSQL authorization schema."""
    return AuthorizationScenario(postgres=postgres)


@given("a production access-policy schema", target_fixture="access_policy_schema")
def production_access_policy_schema(postgres: Postgres) -> str:
    """Initialize a production access-policy schema."""
    schema = postgres.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.table")])}
    )
    asyncio.run(initialize_schemas(postgres.connection, config))
    return schema


@when("I apply authorization to an unprotected table")
def apply_unprotected_authorization(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                "schema": database.namespace.schema,
                "table": authorization_context.table,
                "columns": "id_cras text",
            },
        )
    )
    asyncio.run(
        apply_table_authorization(
            database.connection,
            database.namespace.schema,
            authorization_context.table,
            None,
            None,
        )
    )


@when("I apply authorization to a protected table with an identity claim")
def apply_protected_authorization(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    mapping = {"schema": database.namespace.schema}
    asyncio.run(
        execute_sql(
            database.connection,
            "postgres/create_table",
            mapping={
                **mapping,
                "table": authorization_context.table,
                "columns": "id_cras text",
            },
        )
    )
    asyncio.run(
        execute_sql(
            database.connection, "postgres/create_access_policy", mapping=mapping
        )
    )
    asyncio.run(
        apply_table_authorization(
            database.connection,
            database.namespace.schema,
            authorization_context.table,
            [UnitMapping(column="id_cras", unit_type="cras")],
            "preferred_username",
        )
    )


@then("the table has the schema-scoped policy")
def check_schema_scoped_policy(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/policy_names",
            {
                "schema": database.namespace.schema,
                "table": authorization_context.table,
            },
        )
    )
    assert rows == [("schema_scoped",)]


@then("the user role has select access")
def check_user_select_access(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/select_grants",
            {
                "schema": database.namespace.schema,
                "table": authorization_context.table,
            },
        )
    )
    assert rows == [("user",)]


@then("the table has the access-policy policy")
def check_access_policy(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    rows = asyncio.run(
        fetch_fixture_rows(
            database,
            "postgres/policy_names",
            {
                "schema": database.namespace.schema,
                "table": authorization_context.table,
            },
        )
    )
    assert rows == [("access_policy_scoped",)]


@when(
    'I set up a protected visible table with an allowed grant for "alice"',
)
def setup_protected_visible_table(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    schema = access_policy_schema
    asyncio.run(
        execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "visible", "columns": "id_cras text"},
        )
    )
    asyncio.run(
        execute_sql(
            postgres.connection,
            "postgres/setup_unit_rls_visibility",
            mapping={"schema": schema},
        )
    )
    asyncio.run(
        apply_table_authorization(
            postgres.connection,
            schema,
            "visible",
            [UnitMapping(column="id_cras", unit_type="cras")],
            "preferred_username",
        )
    )
    asyncio.run(postgres.connection.commit())


@when('I query the protected table as "alice"')
def query_protected_table(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    asyncio.run(postgres.connection.execute(b'SET ROLE "user"'))
    asyncio.run(
        postgres.connection.execute(
            f"SET app.claim_schemas = '{access_policy_schema}'".encode()
        )
    )
    asyncio.run(
        postgres.connection.execute(b"SET app.claim_preferred_username = 'alice'")
    )


@then("the protected table shows the allowed row")
def check_protected_visible(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres,
            "postgres/select_visible_id_cras",
            {"schema": access_policy_schema},
        )
    )
    assert rows == [("allowed",)]


@when('I delete the access-policy grant for "alice"')
def delete_grant(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    asyncio.run(postgres.connection.execute(b"RESET ROLE"))
    asyncio.run(
        postgres.connection.execute(
            f"DELETE FROM {access_policy_schema}.access_policy WHERE subject = 'alice'".encode()
        )
    )


@then("the protected table shows no rows")
def check_protected_empty(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    asyncio.run(postgres.connection.execute(b'SET ROLE "user"'))
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres,
            "postgres/select_visible_id_cras",
            {"schema": access_policy_schema},
        )
    )
    assert rows == []


@when("I apply authorization to a schema-scoped table")
def apply_scoped_authorization(postgres: Postgres) -> None:
    schema = postgres.namespace.schema
    asyncio.run(
        execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "scoped", "columns": "id text"},
        )
    )
    asyncio.run(
        execute_sql(
            postgres.connection,
            "postgres/insert_scoped_row",
            mapping={"schema": schema},
        )
    )
    asyncio.run(postgres.connection.commit())
    asyncio.run(
        apply_table_authorization(postgres.connection, schema, "scoped", None, None)
    )
    asyncio.run(postgres.connection.commit())
    asyncio.run(
        execute_sql(
            postgres.connection,
            "postgres/grant_user_schema_usage",
            mapping={"schema": schema},
        )
    )
    asyncio.run(postgres.connection.commit())


@when("I query the scoped table with the matching schema claim")
def query_scoped_matching(postgres: Postgres) -> None:
    schema = postgres.namespace.schema
    asyncio.run(postgres.connection.execute(b'SET ROLE "user"'))
    asyncio.run(
        postgres.connection.execute(f"SET app.claim_schemas = '{schema}'".encode())
    )


@then("the scoped table shows the visible row")
def check_scoped_visible(postgres: Postgres) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres,
            "postgres/select_scoped_ids",
            {"schema": postgres.namespace.schema},
        )
    )
    assert rows == [("visible",)]


@when("I query the scoped table with a non-matching schema claim")
def query_scoped_nonmatching(postgres: Postgres) -> None:
    asyncio.run(postgres.connection.execute(b"SET app.claim_schemas = 'other'"))


@then("the scoped table shows no visible rows")
def check_scoped_empty(postgres: Postgres) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres,
            "postgres/select_scoped_ids",
            {"schema": postgres.namespace.schema},
        )
    )
    assert rows == []


@then("the access-log trigger is a security definer")
def check_trigger_definer(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    row = asyncio.run(
        fetch_fixture_one(
            postgres,
            "postgres/log_trigger_is_security_definer",
            {"schema": access_policy_schema},
        )
    )
    assert row == (True,)


@when('I insert an access-policy grant for "123"')
def insert_policy_grant_123(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    schema = access_policy_schema
    asyncio.run(
        postgres.connection.execute(
            f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) VALUES ('123', true, 'cras', '42')".encode()
        )
    )
    asyncio.run(postgres.connection.commit())


@then('the access-log records an insert for "123"')
def check_insert_log(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres, "postgres/access_log_entries", {"schema": access_policy_schema}
        )
    )
    assert rows == [("123", True, "cras", "42", "insert")]


@when('I insert and update the access-policy grant for "456"')
def insert_and_update_grant(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    schema = access_policy_schema
    asyncio.run(
        postgres.connection.execute(
            f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) VALUES ('456', false, 'escola', '7')".encode()
        )
    )
    asyncio.run(postgres.connection.commit())
    asyncio.run(
        postgres.connection.execute(
            f"UPDATE {schema}.access_policy SET is_admin = true WHERE subject = '456'".encode()
        )
    )
    asyncio.run(postgres.connection.commit())


@then('the access-log records an insert and update for "456"')
def check_update_log(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres, "postgres/access_log_entries", {"schema": access_policy_schema}
        )
    )
    assert len(rows) == 2
    assert rows[0] == ("456", False, "escola", "7", "insert")
    assert rows[1] == ("456", False, "escola", "7", "update")


@when('I insert and delete the access-policy grant for "789"')
def insert_and_delete_grant(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    schema = access_policy_schema
    asyncio.run(
        postgres.connection.execute(
            f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) VALUES ('789', false, 'ap', '1')".encode()
        )
    )
    asyncio.run(postgres.connection.commit())
    asyncio.run(
        postgres.connection.execute(
            f"DELETE FROM {schema}.access_policy WHERE subject = '789'".encode()
        )
    )
    asyncio.run(postgres.connection.commit())


@then('the access-log records an insert and delete for "789"')
def check_delete_log(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres, "postgres/access_log_entries", {"schema": access_policy_schema}
        )
    )
    assert len(rows) == 2
    assert rows[0] == ("789", False, "ap", "1", "insert")
    assert rows[1] == ("789", False, "ap", "1", "delete")


@when('I insert a recent access-policy grant for "recent"')
def insert_recent_grant(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    schema = access_policy_schema
    asyncio.run(
        postgres.connection.execute(
            f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) VALUES ('recent', true, 'cras', '1')".encode()
        )
    )
    asyncio.run(postgres.connection.commit())


@when('I insert a stale access-log entry for "stale"')
def insert_stale_log(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    schema = access_policy_schema
    asyncio.run(
        postgres.connection.execute(
            f"INSERT INTO {schema}.access_log (subject, is_admin, unit_type, unit_id, action, changed_at) VALUES ('stale', false, 'escola', '2', 'delete', now() - interval '100 days')".encode()
        )
    )
    asyncio.run(postgres.connection.commit())


@when("I prune the access log with a 90-day retention")
def prune_access_log(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    asyncio.run(
        postgres.connection.execute(
            b"CALL data_proxy.prune_access_log(%s::interval, %s)",
            ("90 days", access_policy_schema),
        )
    )
    asyncio.run(postgres.connection.commit())


@then("only the recent access-log entry remains")
def check_pruned_log(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    rows = asyncio.run(
        fetch_fixture_rows(
            postgres, "postgres/access_log_entries", {"schema": access_policy_schema}
        )
    )
    assert [row[0] for row in rows] == ["recent"]


# --- Shared helpers ---


async def execute_fixture_sql(
    database: Postgres, path: str, mapping: dict[str, str]
) -> AsyncCursor[DatabaseRow]:
    """Execute one integration SQL fixture."""
    return await execute_sql(database.connection, path, mapping=mapping)


async def fetch_fixture_rows(
    database: Postgres, path: str, mapping: dict[str, str]
) -> list[DatabaseRow]:
    """Fetch all rows from one integration SQL fixture."""
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchall()


async def fetch_fixture_one(
    database: Postgres, path: str, mapping: dict[str, str]
) -> DatabaseRow | None:
    """Fetch one row from one integration SQL fixture."""
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchone()


# --- Loading scenarios ---


@dataclass
class LoadingScenario:
    """State shared by one loading scenario."""

    postgres: Postgres
    table_name: str = "people"


@given("a fresh PostgreSQL loading schema", target_fixture="loading_context")
def fresh_loading_schema(postgres: Postgres) -> LoadingScenario:
    """Provide an isolated PostgreSQL loading schema."""
    return LoadingScenario(postgres=postgres)


@when("I incrementally replace partition 10 and remove partition 20")
def incremental_replace_and_remove(loading_context: LoadingScenario) -> None:
    from data_proxy.publication import prepare_tables

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
    from unittest.mock import AsyncMock, patch

    from data_proxy.publication import prepare_tables

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


@when("I create a full table from Silo Parquet")
def create_full_table_from_silo(loading_context: LoadingScenario) -> None:
    from data_proxy.publication import prepare_tables

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


@when("I rebuild a partitioned table from two Silo batches")
def rebuild_partitioned_from_batches(loading_context: LoadingScenario) -> None:
    from data_proxy.publication import prepare_tables

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


@when("I publish a Silo-backed full table")
def publish_silo_backed_table(loading_context: LoadingScenario) -> None:
    from unittest.mock import patch

    from data_proxy.publication import run_publication

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
    from psycopg.sql import SQL

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


@then("the published table contains the Silo data")
def check_published_silo_data(loading_context: LoadingScenario) -> None:
    from tests.helpers import fetch_one

    database = loading_context.postgres
    row = asyncio.run(
        fetch_one(
            database.connection,
            "postgres/select_people_rows",
            mapping={"schema": database.namespace.schema},
        )
    )
    assert row == (10, "name10")


def sync_config_helper(tables: list[TableConfig], schema: str) -> SyncConfig:
    """Build a sync config for loading tests."""
    return SyncConfig(schemas={schema: SchemaConfig(tables=tables)})

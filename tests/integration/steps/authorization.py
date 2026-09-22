import asyncio
from dataclasses import dataclass

from psycopg import AsyncCursor
from pytest_bdd import given, then, when

from data_proxy.authorization import apply_table_authorization
from data_proxy.models import (
    FullTable,
    SchemaConfig,
    SyncConfig,
    UnitMapping,
)
from data_proxy.schema import initialize_schemas
from data_proxy.types import DatabaseRow
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql


@dataclass
class AuthorizationScenario:
    postgres: Postgres
    table: str = "table"


async def execute_fixture_sql(
    database: Postgres, path: str, mapping: dict[str, str]
) -> AsyncCursor[DatabaseRow]:
    return await execute_sql(database.connection, path, mapping=mapping)


async def fetch_fixture_rows(
    database: Postgres, path: str, mapping: dict[str, str]
) -> list[DatabaseRow]:
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchall()


async def fetch_fixture_one(
    database: Postgres, path: str, mapping: dict[str, str]
) -> DatabaseRow | None:
    cursor = await execute_fixture_sql(database, path, mapping)
    return await cursor.fetchone()


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

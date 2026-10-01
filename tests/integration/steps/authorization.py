import asyncio
from dataclasses import dataclass

from psycopg.sql import SQL, Identifier
from pytest_bdd import given, then, when

from data_proxy.authorization import apply_table_authorization
from data_proxy.models import (
    FullTable,
    SchemaConfig,
    SyncConfig,
    UnitMapping,
)
from data_proxy.settings import settings
from data_proxy.types import DatabaseRow
from tests.fixtures.types import Postgres
from tests.helpers import (
    create_access_policy,
    execute_sql,
    fetch_all,
    fetch_one,
    initialize_schemas,
    insert_access_policy,
    set_setting,
)


@dataclass
class AuthorizationScenario:
    postgres: Postgres
    table: str = "table"


def create_table(database: Postgres, table: str, *columns: str) -> None:
    """Create one text-column table in the test schema."""
    asyncio.run(
        execute_sql(
            database,
            "postgres/create_table",
            mapping={
                "schema": database.namespace.identifier,
                "table": Identifier(table),
                "columns": [Identifier(column) for column in columns],
            },
        )
    )


def policy_names(database: Postgres, table: str) -> list[DatabaseRow]:
    """Return the RLS policy names of one table in the test schema."""
    return asyncio.run(
        fetch_all(
            database,
            "postgres/policy_names",
            params={"schema_name": database.namespace.schema, "table_name": table},
        )
    )


def access_log(database: Postgres) -> list[DatabaseRow]:
    """Return the access-log rows of the test schema in change order."""
    return asyncio.run(
        fetch_all(
            database,
            "postgres/access_log_entries",
            mapping={"schema": database.namespace.identifier},
        )
    )


def claim(database: Postgres, **claims: str) -> None:
    """Act as the user role with the given JWT claims."""
    asyncio.run(
        database.connection.execute(
            SQL("SET ROLE {}").format(Identifier(settings.AUTH_USER_ROLE))
        )
    )
    for name, value in claims.items():
        asyncio.run(set_setting(database, f"app.claim_{name}", value))


def select_multi_visible(database: Postgres) -> list[DatabaseRow]:
    """Return the visible multi-mapping rows."""
    return asyncio.run(
        fetch_all(
            database,
            "postgres/select_multi_visible",
            mapping={"schema": database.namespace.identifier},
        )
    )


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
    asyncio.run(initialize_schemas(postgres.backend, config))
    return schema


@when("I apply authorization to an unprotected table")
def apply_unprotected_authorization(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    create_table(database, authorization_context.table, "region_id")
    asyncio.run(
        apply_table_authorization(
            database.backend,
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
    create_table(database, authorization_context.table, "region_id")
    asyncio.run(create_access_policy(database))
    asyncio.run(
        apply_table_authorization(
            database.backend,
            database.namespace.schema,
            authorization_context.table,
            [UnitMapping(column="region_id", unit_type="region")],
            "preferred_username",
        )
    )


@then("the table has the schema-scoped policy")
def check_schema_scoped_policy(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    assert policy_names(database, authorization_context.table) == [("schema_scoped",)]


@then("the user role has select access")
def check_user_select_access(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    rows = asyncio.run(
        fetch_all(
            database,
            "postgres/select_grants",
            params={
                "schema_name": database.namespace.schema,
                "table_name": authorization_context.table,
                "role_name": settings.AUTH_USER_ROLE,
            },
        )
    )
    assert rows == [(settings.AUTH_USER_ROLE,)]


@then("the table has the access-policy policy")
def check_access_policy(
    authorization_context: AuthorizationScenario,
) -> None:
    database = authorization_context.postgres
    assert policy_names(database, authorization_context.table) == [
        ("access_policy_scoped",)
    ]


@when(
    'I set up a protected visible table with an allowed grant for "alice"',
)
def setup_protected_visible_table(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    create_table(postgres, "visible", "region_id")
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/setup_unit_rls_visibility",
            mapping={
                "schema": postgres.namespace.identifier,
                "user_role": Identifier(settings.AUTH_USER_ROLE),
            },
        )
    )
    asyncio.run(
        apply_table_authorization(
            postgres.backend,
            access_policy_schema,
            "visible",
            [UnitMapping(column="region_id", unit_type="region")],
            "preferred_username",
        )
    )
    asyncio.run(postgres.connection.commit())


@when('I query the protected table as "alice"')
def query_protected_table(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    claim(postgres, schemas=access_policy_schema, preferred_username="alice")


@then("the protected table shows the allowed row")
def check_protected_visible(postgres: Postgres) -> None:
    rows = asyncio.run(
        fetch_all(
            postgres,
            "postgres/select_visible_region_id",
            mapping={"schema": postgres.namespace.identifier},
        )
    )
    assert rows == [("allowed",)]


@when('I delete the access-policy grant for "alice"')
def delete_grant(postgres: Postgres) -> None:
    asyncio.run(postgres.connection.execute(b"RESET ROLE"))
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/delete_access_policy",
            mapping={"schema": postgres.namespace.identifier},
            params={"subject": "alice"},
        )
    )


@then("the protected table shows no rows")
def check_protected_empty(postgres: Postgres) -> None:
    rows = asyncio.run(
        fetch_all(
            postgres,
            "postgres/select_visible_region_id",
            mapping={"schema": postgres.namespace.identifier},
        )
    )
    assert rows == []


@when('I set up a protected table with multiple RLS mappings for "alice"')
def setup_multi_rls_table(postgres: Postgres, access_policy_schema: str) -> None:
    """Create rows covered by separate region and group grants."""
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/setup_multi_rls_visibility",
            mapping={
                "schema": postgres.namespace.identifier,
                "user_role": Identifier(settings.AUTH_USER_ROLE),
            },
        )
    )
    asyncio.run(
        apply_table_authorization(
            postgres.backend,
            access_policy_schema,
            "multi_visible",
            [
                UnitMapping(column="region_id", unit_type="region"),
                UnitMapping(column="group_id", unit_type="group"),
            ],
            "preferred_username",
        )
    )
    asyncio.run(postgres.connection.commit())


@when("I apply authorization to a schema-scoped table")
def apply_scoped_authorization(postgres: Postgres) -> None:
    schema = postgres.namespace.schema
    create_table(postgres, "scoped", "id")
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/insert_scoped_row",
            mapping={"schema": postgres.namespace.identifier},
        )
    )
    asyncio.run(
        apply_table_authorization(postgres.backend, schema, "scoped", None, None)
    )
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/grant_user_schema_usage",
            mapping={
                "schema": postgres.namespace.identifier,
                "user_role": Identifier(settings.AUTH_USER_ROLE),
            },
        )
    )
    asyncio.run(postgres.connection.commit())


@when("I query the scoped table with the matching schema claim")
def query_scoped_matching(postgres: Postgres) -> None:
    claim(postgres, schemas=postgres.namespace.schema)


@then("the scoped table shows the visible row")
def check_scoped_visible(postgres: Postgres) -> None:
    rows = asyncio.run(
        fetch_all(
            postgres,
            "postgres/select_scoped_ids",
            mapping={"schema": postgres.namespace.identifier},
        )
    )
    assert rows == [("visible",)]


@when("I query the scoped table with a non-matching schema claim")
def query_scoped_nonmatching(postgres: Postgres) -> None:
    claim(postgres, schemas="other")


@then("the scoped table shows no visible rows")
def check_scoped_empty(postgres: Postgres) -> None:
    rows = asyncio.run(
        fetch_all(
            postgres,
            "postgres/select_scoped_ids",
            mapping={"schema": postgres.namespace.identifier},
        )
    )
    assert rows == []


@then("alice sees only rows matching one of her unit grants")
def check_multi_rls_rows(postgres: Postgres, access_policy_schema: str) -> None:
    """Verify that either RLS mapping can authorize one row."""
    claim(postgres, schemas=access_policy_schema, preferred_username="alice")
    assert select_multi_visible(postgres) == [
        ("other", "group_allowed"),
        ("region_allowed", "other"),
    ]

    asyncio.run(postgres.connection.execute(b"RESET ROLE"))
    claim(postgres, preferred_username="admin")
    assert select_multi_visible(postgres) == []

    asyncio.run(postgres.connection.execute(b"RESET app.claim_preferred_username"))
    assert select_multi_visible(postgres) == []


@then("the access-log trigger is a security definer")
def check_trigger_definer(
    postgres: Postgres,
    access_policy_schema: str,
) -> None:
    row = asyncio.run(
        fetch_one(
            postgres,
            "postgres/log_trigger_is_security_definer",
            params={"schema_name": access_policy_schema},
        )
    )
    assert row == (True,)


@when('I insert an access-policy grant for "123"')
def insert_policy_grant_123(postgres: Postgres) -> None:
    asyncio.run(insert_access_policy(postgres, "123", "region", "42"))
    asyncio.run(postgres.connection.commit())


@then('the access-log records an insert for "123"')
def check_insert_log(postgres: Postgres) -> None:
    assert access_log(postgres) == [("123", "region", "42", "insert")]


@when('I insert and update the access-policy grant for "456"')
def insert_and_update_grant(postgres: Postgres) -> None:
    asyncio.run(insert_access_policy(postgres, "456", "group", "7"))
    asyncio.run(postgres.connection.commit())
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/update_access_policy",
            mapping={"schema": postgres.namespace.identifier},
            params={"subject": "456", "unit_id": "8"},
        )
    )
    asyncio.run(postgres.connection.commit())


@then('the access-log records an insert and update for "456"')
def check_update_log(postgres: Postgres) -> None:
    assert access_log(postgres) == [
        ("456", "group", "7", "insert"),
        ("456", "group", "7", "update"),
    ]


@when('I insert and delete the access-policy grant for "789"')
def insert_and_delete_grant(postgres: Postgres) -> None:
    asyncio.run(insert_access_policy(postgres, "789", "ap", "1"))
    asyncio.run(postgres.connection.commit())
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/delete_access_policy",
            mapping={"schema": postgres.namespace.identifier},
            params={"subject": "789"},
        )
    )
    asyncio.run(postgres.connection.commit())


@then('the access-log records an insert and delete for "789"')
def check_delete_log(postgres: Postgres) -> None:
    assert access_log(postgres) == [
        ("789", "ap", "1", "insert"),
        ("789", "ap", "1", "delete"),
    ]


@when('I insert a recent access-policy grant for "recent"')
def insert_recent_grant(postgres: Postgres) -> None:
    asyncio.run(insert_access_policy(postgres, "recent", "region", "1"))
    asyncio.run(postgres.connection.commit())


@when('I insert a stale access-log entry for "stale"')
def insert_stale_log(postgres: Postgres) -> None:
    asyncio.run(
        execute_sql(
            postgres,
            "postgres/insert_access_log",
            mapping={"schema": postgres.namespace.identifier},
            params={
                "subject": "stale",
                "unit_type": "group",
                "unit_id": "2",
                "action": "delete",
                "age": "100 days",
            },
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
def check_pruned_log(postgres: Postgres) -> None:
    assert access_log(postgres) == [("recent", "region", "1", "insert")]

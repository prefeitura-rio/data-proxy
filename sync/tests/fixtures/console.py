"""The console schema, built from the real migrations and templates in the test transaction."""

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Final
from uuid import uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier

from sync.settings import settings
from sync.templates import render_template
from tests.constants import POSTGRES_URL, TEST_SQL_DIR
from tests.fixtures.types import Postgres
from tests.helpers import act_as_token, helm_sql, scalar

MIGRATIONS: Final = sorted(
    (Path(__file__).parents[3] / "console" / "migrations").glob("*.sql")
)

CONSOLE_TEMPLATES: Final = (
    "create_console_functions",
    "create_console_config",
    "create_console_triggers",
)


def migration_up(path: Path) -> str:
    """Return the statements of the Up part of a goose migration."""
    text = path.read_text()
    up = text.split("-- +goose Up", 1)[1]

    return up.split("-- +goose Down", 1)[0]


async def migrate(pg: Postgres, files: list[Path]) -> None:
    """Run the Up part of each migration, in order, in the test transaction."""
    for path in files:
        await pg.connection.execute(migration_up(path).encode())


async def install_console(pg: Postgres) -> None:
    """Build the console as the provision job does: the migrations, the functions and the grants."""
    await migrate(pg, MIGRATIONS)

    for name in CONSOLE_TEMPLATES:
        await pg.connection.execute(helm_sql(name).encode())

    await pg.connection.execute(
        helm_sql(
            "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
        ).encode()
    )

    for name, mapping in (
        ("grant_console_usage", {"user_role": Identifier(settings.AUTH_USER_ROLE)}),
        (
            "grant_app_usage",
            {
                "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
                "anonymous_role": Identifier("anon"),
                "user_role": Identifier(settings.AUTH_USER_ROLE),
            },
        ),
    ):
        await pg.connection.execute(helm_sql(name, mapping).encode())


@pytest.fixture
async def console_api(rbac: Postgres, dbos_schema: str) -> Postgres:
    """Give the test the console schema over the DBOS tables and the real rules of rbac.toml."""
    await install_console(rbac)

    return rbac


async def add_schema(
    pg: Postgres, name: str, *, claim: str | None = None, provisioned: bool = True
) -> None:
    """Add a schema to the console. A provisioned schema has the access policy table that the provision job makes."""
    await pg.connection.execute(
        "INSERT INTO console.schemas (name, claim) VALUES (%s, %s)", (name, claim)
    )

    if provisioned:
        await pg.connection.execute(
            SQL(
                "CREATE SCHEMA IF NOT EXISTS {}; CREATE TABLE {}.access_policy (subject text)"
            ).format(Identifier(name), Identifier(name))
        )


async def add_table(
    pg: Postgres,
    name: str,
    schema: str,
    *,
    cache_ttl: int | None = None,
    rls: tuple[str, str] | None = None,
) -> None:
    """Add a table of a schema, with a row rule when `rls` has a column and a unit type."""
    await pg.connection.execute(
        "INSERT INTO console.tables (name, schema, cache_ttl) VALUES (%s, %s, %s)",
        (name, schema, cache_ttl),
    )

    if rls is not None:
        await pg.connection.execute(
            "INSERT INTO console.rls (table_name, column_name, unit_type) VALUES (%s, %s, %s)",
            (name, *rls),
        )


async def as_token(
    pg: Postgres,
    role: str,
    schemas: list[str],
    *,
    user: str = "ana",
    database_role: bool = False,
) -> None:
    """Act as a token: the role and the schemas that rbac reads, and the user name of the claims.

    With `database_role`, the connection also switches to the database role of the requests, so
    the row policies of the console tables apply, as they do for PostgREST.
    """
    await act_as_token(pg, role, ",".join(schemas))
    await pg.connection.execute(
        "SELECT set_config('request.jwt.claims', %s, false)",
        (json.dumps({"preferred_username": user}),),
    )

    if database_role:
        await pg.connection.execute(
            SQL(
                "GRANT USAGE ON SCHEMA rbac TO {role}; "
                "GRANT EXECUTE ON FUNCTION rbac.can(text), rbac.mine(text) TO {role}; "
                "SET LOCAL ROLE {role}"
            ).format(role=Identifier(settings.AUTH_USER_ROLE))
        )


async def refusal(pg: Postgres, sql: str, *params: object) -> str | None:
    """Run a statement that the database may refuse, and return the SQLSTATE of the refusal.

    The statement runs in a savepoint, so the test transaction stays usable.
    """
    try:
        async with pg.connection.transaction():
            await pg.connection.execute(sql.encode(), params)
    except psycopg.Error as error:
        return error.sqlstate

    return None


def migration_down(path: Path) -> str:
    """Return the statements of the Down part of a goose migration, or nothing when it has none."""
    parts = path.read_text().split("-- +goose Down", 1)

    return parts[1] if len(parts) == 2 else ""


def migrations_until(number: int) -> list[Path]:
    """Return the migrations up to and with the number, in order."""
    return [path for path in MIGRATIONS if int(path.name.split("_", 1)[0]) <= number]


def migration(number: int) -> Path:
    """Return the migration with the number."""
    [path] = [path for path in MIGRATIONS if int(path.name.split("_", 1)[0]) == number]

    return path


async def grant_console(pg: Postgres) -> None:
    """Give the role of the requests its rights on the console tables that exist now."""
    await pg.connection.execute(
        helm_sql(
            "grant_console_usage", {"user_role": Identifier(settings.AUTH_USER_ROLE)}
        ).encode()
    )


@pytest.fixture
async def claim_database() -> AsyncIterator[tuple[AsyncConnection, AsyncConnection]]:
    """Give two committed connections to a scratch database that has the console tables, with one schema and one table.

    A test of a lock needs committed rows and two sessions, and the test transaction of the other fixtures gives neither.
    """
    name = f"claim_{uuid4().hex[:8]}"
    async with await AsyncConnection.connect(
        f"{POSTGRES_URL}/postgres", autocommit=True
    ) as admin:
        await admin.execute(SQL("CREATE DATABASE {}").format(Identifier(name)))

        try:
            async with AsyncExitStack() as stack:
                first, second = [
                    await stack.enter_async_context(
                        await AsyncConnection.connect(
                            f"{POSTGRES_URL}/{name}", autocommit=True
                        )
                    )
                    for _ in range(2)
                ]
                await first.execute(b"CREATE SCHEMA console")
                for number in (2, 3, 11):
                    await first.execute(migration_up(migration(number)).encode())
                await first.execute(
                    render_template("postgres/claim_fixture", {}, root=TEST_SQL_DIR)
                )

                yield first, second
        finally:
            await admin.execute(
                SQL("DROP DATABASE {} WITH (FORCE)").format(Identifier(name))
            )


async def two_schemas(pg: Postgres) -> None:
    """Add two provisioned schemas, one table in each, and a row rule on the first."""
    await add_schema(pg, "sales", claim="cpf")
    await add_schema(pg, "hr", claim="cpf")
    await add_table(pg, "p.d.orders", "sales", cache_ttl=60, rls=("cpf", "cpf"))
    await add_table(pg, "p.d.staff", "hr")


async def start(
    pg: Postgres, schemas: list[str], tables: list[str] | None = None
) -> str:
    """Start a run as an administrator of every schema, and return its id."""
    await as_token(pg, "dp_admin", ["sales", "hr"])
    run = await scalar(
        pg,
        "SELECT console.start_sync(%s, %s)",
        schemas,
        [] if tables is None else tables,
    )
    assert isinstance(run, str)

    return run


async def seed(pg: Postgres) -> None:
    """Add two schemas with a claim, two tables, a partitioned table and two row rules."""
    await add_schema(pg, "sales", claim="cpf")
    await add_schema(pg, "hr", claim="cpf")
    await add_table(pg, "p.d.orders", "sales", cache_ttl=60, rls=("cpf", "cpf"))
    await pg.connection.execute(
        "INSERT INTO console.rls (table_name, column_name, unit_type) VALUES ('p.d.orders', 'cnpj', 'cnpj')"
    )
    await add_table(pg, "p.d.staff", "hr")
    await pg.connection.execute(
        """
        INSERT INTO console.tables (name, schema, strategy, n, fallback, partitioning, sort)
        VALUES ('p.d.events', 'sales', 'partitioned', 4, TRUE, '[{"column": "dt"}]', ARRAY['dt', 'id'])
        """
    )


async def add_policy_schema(
    pg: Postgres, name: str, grants: dict[str, list[tuple[str, str]]], claim: str
) -> None:
    """Add a provisioned schema whose access policy has the given grants for each subject."""
    await add_schema(pg, name, claim=claim)
    await pg.connection.execute(
        SQL(
            "ALTER TABLE {}.access_policy ADD COLUMN unit_type text, ADD COLUMN unit_id text"
        ).format(Identifier(name))
    )
    for subject, units in grants.items():
        for unit_type, unit_id in units:
            await pg.connection.execute(
                SQL("INSERT INTO {}.access_policy VALUES (%s, %s, %s)").format(
                    Identifier(name)
                ),
                (subject, unit_type, unit_id),
            )

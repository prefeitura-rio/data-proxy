import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from uuid import uuid4

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier

from sync.templates import render_template
from tests.constants import POSTGRES_URL, TEST_SQL_DIR
from tests.fixtures.console import (
    add_schema,
    as_token,
    grant_console,
    migrate,
    migration,
    migration_down,
    migration_up,
    migrations_until,
    refusal,
)
from tests.fixtures.types import Postgres
from tests.helpers import scalar

CHECK_VIOLATION = "23514"


async def names(pg: Postgres, sql: str) -> list[object]:
    """Return the first column of every row of a statement."""
    cursor = await pg.connection.execute(sql.encode())

    return [row[0] for row in await cursor.fetchall()]


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


BLOCKED_SECONDS = 0.5
ADD_RULE = "INSERT INTO console.rls (table_name, column_name, unit_type) VALUES ('p.d.t', 'id', 'cpf')"
CLEAR_CLAIM = "UPDATE console.schemas SET claim = NULL WHERE name = 's'"


@pytest.mark.postgres
class TestClaimChecksUnderConcurrency:
    """A schema with a row rule keeps its claim, also when two editors act together."""

    async def test_a_claim_cleared_while_a_rule_is_added_waits_and_is_refused(
        self, claim_database: tuple[AsyncConnection, AsyncConnection]
    ) -> None:
        """Both checks read what the other has not committed, so without a lock both pass and the schema ends with a rule and no claim."""
        first, second = claim_database

        async with first.transaction():
            await first.execute(ADD_RULE)
            clearing = asyncio.create_task(second.execute(CLEAR_CLAIM))
            done, _ = await asyncio.wait({clearing}, timeout=BLOCKED_SECONDS)
            assert len(done) == 0, "The change did not wait for the open transaction"

        with pytest.raises(psycopg.errors.CheckViolation, match="needs a claim"):
            await clearing

    async def test_a_rule_added_while_a_claim_is_cleared_waits_and_is_refused(
        self, claim_database: tuple[AsyncConnection, AsyncConnection]
    ) -> None:
        """The same race in the other order: the rule must see the cleared claim."""
        first, second = claim_database

        async with first.transaction():
            await first.execute(CLEAR_CLAIM)
            adding = asyncio.create_task(second.execute(ADD_RULE))
            done, _ = await asyncio.wait({adding}, timeout=BLOCKED_SECONDS)
            assert len(done) == 0, "The change did not wait for the open transaction"

        with pytest.raises(psycopg.errors.CheckViolation, match="has no claim"):
            await adding


@pytest.mark.postgres
class TestMigrationsOnExistingData:
    """A migration that meets the rows of an older version keeps them and changes what it says it changes."""

    async def test_00005_scopes_the_tables_by_schema_without_losing_a_row(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Before it, a token reads every schema. After it, a token reads the schemas of its claim."""
        await migrate(rbac, migrations_until(4))
        await grant_console(rbac)
        for name in ("sales", "hr"):
            await add_schema(rbac, name, claim="cpf", provisioned=False)
        await as_token(rbac, "dp_console", ["sales"], database_role=True)

        assert await names(rbac, "SELECT name FROM console.schemas ORDER BY name") == [
            "hr",
            "sales",
        ]

        await rbac.connection.execute(b"RESET ROLE")
        await migrate(rbac, [migration(5)])
        await as_token(rbac, "dp_console", ["sales"], database_role=True)

        assert await names(rbac, "SELECT name FROM console.schemas ORDER BY name") == [
            "sales"
        ]
        await rbac.connection.execute(b"RESET ROLE")
        assert await scalar(rbac, "SELECT count(*) FROM console.schemas") == 2

    @pytest.mark.parametrize(
        ("state", "partitions"),
        [
            pytest.param(
                '{"signature": "s", "partitions": {"a": {}, "b": {}, "c": {}}}',
                3,
                id="three-partitions",
            ),
            pytest.param('{"signature": "s", "partitions": {}}', 0, id="no-partition"),
            pytest.param('{"signature": "s"}', 0, id="no-partitions-key"),
            pytest.param(
                '{"signature": "s", "partitions": []}',
                0,
                id="partitions-that-are-not-an-object",
            ),
        ],
    )
    async def test_00006_counts_the_partitions_of_a_state_that_it_finds(
        self, rbac: Postgres, dbos_schema: str, state: str, partitions: int
    ) -> None:
        """Count the keys of the partitions object, and say 0 when there is none."""
        await migrate(rbac, migrations_until(5))
        await add_schema(rbac, "sales", claim="cpf", provisioned=False)
        await rbac.connection.execute(
            b"INSERT INTO console.tables (name, schema) VALUES ('p.d.t', 'sales')"
        )
        await rbac.connection.execute(
            b"INSERT INTO data_proxy.state (table_name, state) VALUES ('p.d.t', %s::jsonb)",
            (state,),
        )

        await migrate(rbac, [migration(6)])
        await as_token(rbac, "dp_console", ["sales"])

        cursor = await rbac.connection.execute(
            b"SELECT synced, signature, partition_count FROM console.synced WHERE name = 'p.d.t'"
        )
        assert await cursor.fetchall() == [(True, "s", partitions)]

    async def test_00006_says_a_table_with_no_state_is_not_synced(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Show no signature and no partition for a table that has not run."""
        await migrate(rbac, migrations_until(6))
        await add_schema(rbac, "sales", claim="cpf", provisioned=False)
        await rbac.connection.execute(
            b"INSERT INTO console.tables (name, schema) VALUES ('p.d.new', 'sales')"
        )
        await as_token(rbac, "dp_console", ["sales"])

        cursor = await rbac.connection.execute(
            b"SELECT synced, signature, partition_count FROM console.synced"
        )

        assert await cursor.fetchall() == [(False, None, 0)]

    async def test_00007_keeps_the_schemas_and_limits_a_new_name_to_the_length_of_its_role(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """A schema gets the role schema_<name>, so a name beyond 56 characters is refused from now on."""
        await migrate(rbac, migrations_until(6))
        await add_schema(rbac, "x" * 56, provisioned=False)
        long_name = "y" * 60
        assert (
            await refusal(
                rbac, "INSERT INTO console.schemas (name) VALUES (%s)", long_name
            )
            is None
        )
        await rbac.connection.execute(
            b"DELETE FROM console.schemas WHERE name = %s", (long_name,)
        )

        await migrate(rbac, [migration(7)])

        assert await scalar(rbac, "SELECT count(*) FROM console.schemas") == 1
        assert (
            await refusal(
                rbac, "INSERT INTO console.schemas (name) VALUES (%s)", long_name
            )
            == CHECK_VIOLATION
        )
        assert (
            await refusal(
                rbac, "INSERT INTO console.schemas (name) VALUES (%s)", "z" * 56
            )
            is None
        )

    async def test_00009_records_the_changes_after_it_and_not_the_rows_before_it(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Start the history at the migration: the old rows stay, with no invented past."""
        await migrate(rbac, migrations_until(8))
        await add_schema(rbac, "sales", claim="cpf", provisioned=False)
        await rbac.connection.execute(
            b"INSERT INTO console.tables (name, schema) VALUES ('p.d.t', 'sales')"
        )

        await migrate(rbac, [migration(9)])

        assert await scalar(rbac, "SELECT count(*) FROM console.config_history") == 0
        await rbac.connection.execute(
            b"UPDATE console.tables SET cache_ttl = 5 WHERE name = 'p.d.t'"
        )
        cursor = await rbac.connection.execute(
            b"SELECT action, object, schema_name, key FROM console.config_history"
        )
        assert await cursor.fetchall() == [("update", "tables", "sales", "p.d.t")]

    @pytest.mark.parametrize(
        ("schedule_name", "expected"),
        [
            pytest.param("sync-hr", "hr", id="a-schedule-of-a-schema"),
            pytest.param(
                "sync-a_b-2", "a_b-2", id="the-rest-of-the-name-is-the-schema"
            ),
            pytest.param("sync", None, id="the-old-global-schedule"),
            pytest.param("sync-", None, id="a-prefix-without-a-schema"),
            pytest.param("nightly", None, id="another-schedule"),
            pytest.param("Sync-hr", None, id="the-prefix-has-lower-case-letters"),
        ],
    )
    async def test_00010_reads_the_schema_from_the_name_of_a_schedule(
        self, rbac: Postgres, schedule_name: str, expected: str | None
    ) -> None:
        """The one function that knows the prefix gives the schema, or nothing."""
        await migrate(rbac, migrations_until(10))

        assert (
            await scalar(rbac, "SELECT console.schedule_schema(%s)", schedule_name)
            == expected
        )

    async def test_00010_shows_the_schedules_of_the_schemas_that_the_token_reaches(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Before it, a token sees the global schedule. After it, the token sees one schedule for each of its schemas."""
        await migrate(rbac, migrations_until(9))
        await grant_console(rbac)
        for name in ("sales", "hr"):
            await add_schema(rbac, name, claim="cpf", provisioned=False)
        for name in ("sync", "sync-sales", "sync-hr"):
            await rbac.connection.execute(
                "INSERT INTO dbos.workflow_schedules (schedule_id, schedule_name, workflow_name, schedule, context) VALUES (%s, %s, 'run_sync', '0 2 * * *', '{}')",
                (f"id-{name}", name),
            )
        await as_token(rbac, "dp_console", ["sales"], database_role=True)

        assert await names(
            rbac, "SELECT name FROM console.schedules ORDER BY name"
        ) == [
            "sync",
            "sync-hr",
            "sync-sales",
        ]

        await rbac.connection.execute(b"RESET ROLE")
        await migrate(rbac, [migration(10)])
        await grant_console(rbac)
        await as_token(rbac, "dp_console", ["sales"], database_role=True)

        cursor = await rbac.connection.execute(
            b"SELECT name, schema FROM console.schedules ORDER BY name"
        )
        assert await cursor.fetchall() == [("sync-sales", "sales")]


@pytest.mark.postgres
class TestMigrationOrder:
    """The migrations apply in order on an empty database, and the Down parts undo them."""

    async def test_every_migration_applies_in_order_on_an_empty_database(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Build every table and every view of the console."""
        await migrate(rbac, migrations_until(9))

        tables = await names(
            rbac,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'console' ORDER BY 1",
        )
        assert {
            "config_history",
            "errors",
            "health",
            "logs",
            "overview",
            "queues",
            "rls",
            "runs",
            "schedules",
            "schemas",
            "steps",
            "synced",
            "tables",
            "tasks",
        } <= set(tables)

    async def test_the_down_parts_run_in_reverse_order_and_leave_no_console_schema(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Go back to the start with the Down parts alone."""
        await migrate(rbac, migrations_until(11))

        for number in range(11, 0, -1):
            down = migration_down(migration(number))
            if len(down.strip()) > 0:
                await rbac.connection.execute(down.encode())

        assert (
            await scalar(
                rbac, "SELECT count(*) FROM pg_namespace WHERE nspname = 'console'"
            )
            == 0
        )

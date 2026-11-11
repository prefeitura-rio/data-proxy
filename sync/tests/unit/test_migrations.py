import asyncio

import psycopg
import pytest
from psycopg import AsyncConnection

from tests.fixtures.console import (
    add_schema,
    as_token,
    grant_app_usage,
    grant_console_reads,
    migrate,
    migration,
    migration_down,
    migrations_until,
    refusal,
)
from tests.fixtures.types import Postgres
from tests.helpers.core import names, scalar

CHECK_VIOLATION = "23514"


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
        await grant_console_reads(rbac)
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

    async def test_00012_puts_every_schema_in_the_default_project_and_keeps_its_name(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """No physical schema changes its name, so no data moves."""
        await migrate(rbac, migrations_until(11))
        await add_schema(rbac, "sales", claim="cpf", provisioned=False)
        await rbac.connection.execute(
            b"INSERT INTO console.tables (name, schema) VALUES ('p.d.t', 'sales')"
        )

        await migrate(rbac, [migration(12)])

        cursor = await rbac.connection.execute(
            b"SELECT name, project, short_name FROM console.schemas"
        )
        assert await cursor.fetchall() == [("sales", "default", "sales")]
        assert await names(rbac, "SELECT name FROM console.projects") == ["default"]
        assert await names(rbac, "SELECT schema FROM console.tables") == ["sales"]

    async def test_00013_gives_require_claim_its_definer_rights_back_with_a_pinned_search_path(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Migration 00011 replaced the function and dropped both settings that 00005 had set."""
        found = (
            "SELECT prosecdef, proconfig FROM pg_proc WHERE proname = 'require_claim'"
        )
        await migrate(rbac, migrations_until(12))

        cursor = await rbac.connection.execute(found.encode())
        assert await cursor.fetchall() == [(False, None)]

        await migrate(rbac, [migration(13)])

        cursor = await rbac.connection.execute(found.encode())
        assert await cursor.fetchall() == [(True, ["search_path=pg_catalog, pg_temp"])]

    async def test_00014_keeps_the_counts_of_the_runs_and_names_each_schema_once(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """The new view counts the tasks with subqueries, and a token still counts only the tasks of its own schemas. The old view named a schema once for each scope row of the run."""
        await migrate(rbac, migrations_until(13))
        await grant_console_reads(rbac)
        await grant_app_usage(rbac)
        for name in ("sales", "hr"):
            await add_schema(rbac, name, claim="cpf", provisioned=False)
        workflows = [
            ("run-1", "run_sync", None, None, "SUCCESS"),
            ("run-1-a", "dump_task", "run-1", "sales", "SUCCESS"),
            ("run-1-b", "dump_task", "run-1", "sales", "ERROR"),
            ("run-1-c", "dump_task", "run-1", "hr", "ERROR"),
            ("run-2", "run_sync", None, None, "SUCCESS"),
            ("run-2-a", "dump_task", "run-2", "hr", "SUCCESS"),
        ]
        for number, (uuid, name, parent, partition, status) in enumerate(workflows):
            await rbac.connection.execute(
                "INSERT INTO dbos.workflow_status (workflow_uuid, status, name, authenticated_user, queue_name, "
                "queue_partition_key, parent_workflow_id, created_at, updated_at) VALUES (%s, %s, %s, 'seed', 'q', %s, %s, %s, %s)",
                (uuid, status, name, partition, parent, 1000 + number, 1000 + number),
            )
        for uuid, run, schema in (
            ("run-1", "run-1", "sales"),
            ("run-1-a", "run-1", "sales"),
            ("run-1-b", "run-1", "sales"),
            ("run-1-c", "run-1", "hr"),
            ("run-2", "run-2", "hr"),
            ("run-2-a", "run-2", "hr"),
        ):
            await rbac.connection.execute(
                "INSERT INTO data_proxy.scope (workflow_id, run_id, schema_name) VALUES (%s, %s, %s)",
                (uuid, run, schema),
            )
        read = b"SELECT id, task_count, failed_task_count, schemas FROM console.runs ORDER BY id"

        await as_token(rbac, "dp_console", ["sales"], database_role=True)
        before = await (await rbac.connection.execute(read)).fetchall()

        await rbac.connection.execute(b"RESET ROLE")
        await migrate(rbac, [migration(14)])
        await grant_console_reads(rbac)
        await grant_app_usage(rbac)
        await as_token(rbac, "dp_console", ["sales"], database_role=True)
        after = await (await rbac.connection.execute(read)).fetchall()

        assert before == [("run-1", 2, 1, ["sales", "sales", "sales"])]
        assert after == [("run-1", 2, 1, ["sales"])]

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
        self,
        rbac: Postgres,
        dbos_schema: str,
        schedule_name: str,
        expected: str | None,
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
        await grant_console_reads(rbac)
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
        await grant_console_reads(rbac)
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
        await migrate(rbac, migrations_until(14))

        for number in range(14, 0, -1):
            down = migration_down(migration(number))
            if len(down.strip()) > 0:
                await rbac.connection.execute(down.encode())

        assert (
            await scalar(
                rbac, "SELECT count(*) FROM pg_namespace WHERE nspname = 'console'"
            )
            == 0
        )

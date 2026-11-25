import asyncio
from typing import ClassVar

import psycopg
import pytest
from psycopg import AsyncConnection

from tests.fixtures.types import Postgres
from tests.helpers.core import scalar
from tests.helpers.migrations import (
    GOOSE,
    MIGRATIONS,
    ROLES,
    description_lines,
    goose,
    migration_files,
    migration_sql,
    scratch_url,
)
from tests.helpers.rbac import relations_with

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


CONSOLE_WRITES = {"projects", "schemas", "tables"}


@pytest.mark.postgres
class TestGrantsOfTheMigrations:
    """The grants of the migrations give each request role its rights, and no more."""

    async def test_the_role_of_the_tokens_reads_the_console_and_writes_only_its_configuration(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """Every token has this role, so it reads the console and changes the configuration tables, and no other table."""
        cursor = await rbac.connection.execute(
            b"""
            SELECT count(*) FROM pg_class AS c
            INNER JOIN pg_namespace AS n ON c.relnamespace = n.oid
            WHERE n.nspname = 'console' AND c.relkind IN ('r', 'p', 'v')
                AND c.relname <> 'rls'
                AND NOT has_table_privilege('dp_api', c.oid, 'SELECT')
            """
        )
        assert await cursor.fetchone() == (0,)
        assert (
            await scalar(
                rbac, "SELECT has_schema_privilege('dp_api', 'console', 'USAGE')"
            )
            is True
        )
        assert (
            await scalar(
                rbac, "SELECT has_table_privilege('dp_api', 'console.rls', 'SELECT')"
            )
            is False
        )
        assert await relations_with(rbac, "INSERT", any_column=True) == CONSOLE_WRITES
        assert await relations_with(rbac, "DELETE", any_column=False) == CONSOLE_WRITES
        assert await relations_with(
            rbac, "UPDATE", any_column=True
        ) == CONSOLE_WRITES - {"projects"}

    async def test_the_role_of_the_tokens_runs_the_function_that_filters_the_workflows(
        self, rbac: Postgres
    ) -> None:
        """The console views call data_proxy.sees with the rights of the caller."""
        assert (
            await scalar(
                rbac,
                "SELECT has_function_privilege('dp_api', 'data_proxy.sees(text, text)', 'EXECUTE')",
            )
            is True
        )

    @pytest.mark.parametrize("role", ["anon", "dp_api"])
    async def test_every_request_role_runs_the_pre_request_function(
        self, rbac: Postgres, role: str
    ) -> None:
        """PostgREST calls the function before each request, also one that has no token."""
        assert (
            await scalar(
                rbac,
                "SELECT has_function_privilege(%s, 'data_proxy.pre_request()', 'EXECUTE')",
                role,
            )
            is True
        )

    @pytest.mark.parametrize(
        "function",
        ["reload_postgrest()", "wait_for_standbys(interval)"],
    )
    async def test_no_request_role_runs_the_reload_functions(
        self, rbac: Postgres, function: str
    ) -> None:
        """Only the jobs and the sync reload PostgREST, so the public role and the request roles can't."""
        for role in ("anon", "dp_api", "public"):
            assert (
                await scalar(
                    rbac,
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    role,
                    f"data_proxy.{function}",
                )
                is False
            )

    async def test_a_table_and_a_function_of_a_later_migration_reach_the_role_of_the_tokens(
        self, rbac: Postgres
    ) -> None:
        """A migration that adds a console object needs no grant of its own, because of the default privileges."""
        await rbac.connection.execute(
            b"CREATE TABLE console.later (id integer); "
            b"CREATE FUNCTION console.later_fn() RETURNS integer LANGUAGE sql AS 'SELECT 1'"
        )

        assert (
            await scalar(
                rbac, "SELECT has_table_privilege('dp_api', 'console.later', 'SELECT')"
            )
            is True
        )
        assert (
            await scalar(
                rbac,
                "SELECT has_function_privilege('dp_api', 'console.later_fn()', 'EXECUTE')",
            )
            is True
        )

    async def test_the_grants_name_no_queue_and_no_schedule(self) -> None:
        """No token changes a queue or a schedule, so the grants never name one."""
        text = (MIGRATIONS / "console" / "00014_grants.sql").read_text()

        assert "queues" not in text
        assert "schedules" not in text


@pytest.mark.postgres
@pytest.mark.skipif(GOOSE is None, reason="goose is not installed")
class TestMigrationRun:
    """The migrations that the Job migrate-db runs, run on empty databases."""

    async def test_every_migration_applies_in_order_on_an_empty_database(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """Both folders apply, and the console, the state and the signer table exist afterward."""
        app, catalog = scratch_databases

        assert (await goose("console", app, "up")).returncode == 0
        assert (await goose("ducklake", catalog, "up")).returncode == 0

        async with (
            await AsyncConnection.connect(scratch_url(app)) as connection,
            await AsyncConnection.connect(scratch_url(catalog)) as catalog_connection,
        ):
            cursor = await connection.execute(
                b"SELECT to_regclass('console.schemas'), to_regclass('data_proxy.state'), to_regproc('rbac.can')"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert all(found is not None for found in row)
            cursor = await catalog_connection.execute(
                b"SELECT to_regclass('signer.keys')"
            )
            assert await cursor.fetchone() != (None,)

    async def test_a_second_run_changes_nothing(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """Goose finds every version applied."""
        app, catalog = scratch_databases
        await goose("console", app, "up")
        await goose("ducklake", catalog, "up")

        again = await goose("console", app, "up")

        assert again.returncode == 0
        assert "no migrations to run" in again.stderr + again.stdout

    async def test_an_unset_role_name_stops_the_run(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """A grant names its role in quotes, so an empty name is an error and not a role with no name."""
        app, _ = scratch_databases
        roles = {key: value for key, value in ROLES.items() if key != "AUTH_USER_ROLE"}

        result = await goose("console", app, "up", env=roles)

        assert result.returncode != 0
        assert "zero-length delimited identifier" in result.stderr + result.stdout

    async def test_the_last_migration_goes_down_and_up_again(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """A migration that has a Down part leaves the database as it was before the Up part."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        assert (await goose("console", app, "down")).returncode == 0
        assert (await goose("console", app, "up")).returncode == 0

    async def test_the_grant_all_migration_goes_down_and_restores_the_old_answer(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """A role with grant_all reaches a schema that no claim names, until the Down part runs."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async def reaches_schema() -> object:
            async with await AsyncConnection.connect(scratch_url(app)) as connection:
                await connection.execute(
                    b"SELECT set_config('rbac.permissions', ',grant_all,', false)"
                )
                cursor = await connection.execute(b"SELECT rbac.mine('s1')")
                row = await cursor.fetchone()
                assert row is not None
                return row[0]

        assert await reaches_schema() is True
        assert (await goose("console", app, "down-to", "15")).returncode == 0
        assert await reaches_schema() is False

    async def test_the_rename_migration_goes_down_to_the_trigger_of_inserts_and_deletes(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """The reconcile of the schedules follows a rename of a schema, until the Down part runs."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async def trigger_definition() -> object:
            async with await AsyncConnection.connect(scratch_url(app)) as connection:
                cursor = await connection.execute(
                    b"SELECT pg_get_triggerdef(oid) FROM pg_trigger "
                    b"WHERE tgname = 'reconcile_schedules'"
                )
                row = await cursor.fetchone()
                assert row is not None
                return row[0]

        assert "INSERT OR DELETE OR UPDATE OF name, project, short_name" in str(
            await trigger_definition()
        )
        assert (await goose("console", app, "down-to", "16")).returncode == 0
        assert "AFTER INSERT OR DELETE ON console.schemas" in str(
            await trigger_definition()
        )

    async def test_the_rule_group_migration_goes_down_and_up_again(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """Drop the column and the replace function on the way down, and bring both back on the way up."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async def parts() -> tuple[bool, bool]:
            async with await AsyncConnection.connect(scratch_url(app)) as connection:
                cursor = await connection.execute(
                    b"SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'console' AND table_name = 'rls' AND column_name = 'rule_group'), to_regprocedure('console.replace_rls(text, jsonb)') IS NOT NULL"
                )
                row = await cursor.fetchone()
                assert row is not None
                return (bool(row[0]), bool(row[1]))

        assert await parts() == (True, True)
        assert (await goose("console", app, "down-to", "17")).returncode == 0
        assert await parts() == (False, False)
        assert (await goose("console", app, "up")).returncode == 0
        assert await parts() == (True, True)

    async def test_the_catalog_and_default_project_migrations_go_down_and_up_again(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """Down to version 18 drops the catalog view and the ltree extension, and gives the delete policy its old text."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async def state() -> tuple[object, ...]:
            async with await AsyncConnection.connect(scratch_url(app)) as connection:
                cursor = await connection.execute(
                    b"SELECT to_regclass('console.catalog') IS NOT NULL,"
                    b" EXISTS (SELECT FROM pg_extension WHERE extname = 'ltree'),"
                    b" (SELECT qual LIKE '%default%' FROM pg_policies"
                    b"  WHERE schemaname = 'console' AND tablename = 'projects' AND policyname = 'delete_scope')"
                )
                row = await cursor.fetchone()
                assert row is not None
                return row

        assert await state() == (True, True, True)
        assert (await goose("console", app, "down-to", "18")).returncode == 0
        assert await state() == (False, False, False)
        assert (await goose("console", app, "up")).returncode == 0
        assert await state() == (True, True, True)


@pytest.mark.postgres
@pytest.mark.skipif(GOOSE is None, reason="goose is not installed")
class TestProjectSchemaNames:
    """The migration that gives every schema the name `<project>__<schema>`."""

    SEED = (
        b"INSERT INTO console.projects (name) VALUES ('sales');"
        b"INSERT INTO console.schemas (project, short_name) VALUES ('default', 'legacy'), ('sales', 'orders');"
        b"INSERT INTO console.tables (name, schema) VALUES ('p.d.t', 'sales_orders');"
        b"CREATE SCHEMA legacy; CREATE SCHEMA sales_orders; CREATE TABLE sales_orders.t (id int);"
        b"INSERT INTO data_proxy.state VALUES ('sales_orders.t', '{}');"
        b"INSERT INTO data_proxy.scope (workflow_id, run_id, schema_name) VALUES ('w', 'w', 'legacy');"
        b"INSERT INTO data_proxy.errors (reason, fields)"
        b' VALUES (\'x\', \'{"schema": "legacy", "table": "legacy.t"}\');'
        b"INSERT INTO dbos.workflow_schedules (schedule_id, schedule_name, workflow_name, schedule, context)"
        b" VALUES ('i', 'sync-sales_orders', 'run_sync', '0 2 * * *', '{\"schemas\": [\"sales_orders\"]}')"
    )
    STATE = (
        b"SELECT (SELECT array_agg(name ORDER BY name) FROM console.schemas),"
        b" (SELECT schema FROM console.tables),"
        b" (SELECT array_agg(nspname ORDER BY nspname) FROM pg_namespace"
        b"  WHERE nspname IN ('legacy', 'sales_orders', 'default__legacy', 'sales__orders')),"
        b" (SELECT table_name FROM data_proxy.state),"
        b" (SELECT schema_name FROM data_proxy.scope),"
        b" (SELECT fields::text FROM data_proxy.errors)"
    )

    async def state(self, app: str) -> tuple[object, ...]:
        """Read the names that the migration changes."""
        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            row = await (await connection.execute(self.STATE)).fetchone()
            assert row is not None
            return row

    async def test_every_name_changes_and_goes_back_with_the_down_part(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """The schema, the table link, the state, the scope, the errors and the schedule follow the new name, and the Down part restores them."""
        app, _ = scratch_databases
        await goose("console", app, "up-to", "20")
        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            for statement in self.SEED.decode().split(";"):
                await connection.execute(statement.encode())
        old = await self.state(app)

        assert (await goose("console", app, "up")).returncode == 0
        assert await self.state(app) == (
            ["default__legacy", "sales__orders"],
            "sales__orders",
            ["default__legacy", "sales__orders"],
            "sales__orders.t",
            "default__legacy",
            '{"table": "default__legacy.t", "schema": "default__legacy"}',
        )

        assert (await goose("console", app, "down-to", "20")).returncode == 0
        assert await self.state(app) == old

    async def test_the_schedules_go_and_a_reconcile_creates_them_again(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """DBOS pickles the schema list of a schedule, so the migration deletes the schedule and queues the reconcile."""
        app, _ = scratch_databases
        await goose("console", app, "up-to", "20")
        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            for statement in self.SEED.decode().split(";"):
                await connection.execute(statement.encode())

        assert (await goose("console", app, "up")).returncode == 0

        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            schedules = await (
                await connection.execute(
                    b"SELECT count(*) FROM dbos.workflow_schedules"
                )
            ).fetchone()
            queued = await (
                await connection.execute(
                    b"SELECT queue_name FROM dbos.workflow_status WHERE name = 'reconcile_schedules'"
                )
            ).fetchall()

        assert schedules == (0,)
        assert ("schedules",) in queued

    async def test_one_sync_run_for_every_schema_rebuilds_the_generated_objects(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """The functions of a renamed schema name the old schema, so the migration queues one run that rebuilds them."""
        app, _ = scratch_databases
        await goose("console", app, "up-to", "20")
        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            for statement in self.SEED.decode().split(";"):
                await connection.execute(statement.encode())

        assert (await goose("console", app, "up")).returncode == 0

        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            cursor = await connection.execute(
                b"SELECT queue_name, status FROM dbos.workflow_status WHERE name = 'run_sync'"
            )
            rows = await cursor.fetchall()

        assert rows == [("sync", "ENQUEUED")]

    async def test_a_schema_name_of_the_old_form_is_refused(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """The check on the name keeps the form `<project>__<schema>`, also in the project default."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            with pytest.raises(
                psycopg.errors.CheckViolation, match="schemas_name_parts"
            ):
                await connection.execute(
                    b"INSERT INTO console.schemas (name, project, short_name) VALUES ('hr', 'default', 'hr')"
                )

    async def test_a_schema_added_later_gets_the_new_name(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """A project and a short name give the physical name without a name."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            await connection.execute(
                b"INSERT INTO console.schemas (project, short_name) VALUES ('default', 'hr')"
            )
            row = await (
                await connection.execute(b"SELECT name FROM console.schemas")
            ).fetchone()

        assert row == ("default__hr",)

    async def test_two_schemas_of_one_old_name_are_refused(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """The schema `a_b` of the project default and the schema `b` of the project `a` would share the S3 folder `a_b`."""
        app, _ = scratch_databases
        await goose("console", app, "up")

        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            await connection.execute(
                b"INSERT INTO console.projects (name) VALUES ('a')"
            )
            await connection.execute(
                b"INSERT INTO console.schemas (project, short_name) VALUES ('default', 'a_b')"
            )
            with pytest.raises(
                psycopg.errors.UniqueViolation, match="schemas_data_prefix"
            ):
                await connection.execute(
                    b"INSERT INTO console.schemas (project, short_name) VALUES ('a', 'b')"
                )


@pytest.mark.postgres
@pytest.mark.skipif(GOOSE is None, reason="goose is not installed")
class TestAccessSchema:
    """The migration that moves the grants of every schema into one table."""

    SEED = (
        b"INSERT INTO console.projects (name) VALUES ('sales');"
        b"INSERT INTO console.schemas (project, short_name) VALUES ('default', 'legacy'), ('sales', 'orders');"
        b"CREATE TABLE default__legacy.access_policy (subject text NOT NULL, unit_type text, unit_id text);"
        b"CREATE TABLE sales__orders.access_policy (subject text NOT NULL, unit_type text, unit_id text);"
        b"INSERT INTO default__legacy.access_policy VALUES"
        b" ('ana', 'unit', '1'), ('bo', 'unit', '2'), ('carol', NULL, NULL), ('dee', 'unit', NULL);"
        b"INSERT INTO sales__orders.access_policy VALUES ('ana', 'unit', '1'), ('eve', 'unit', '3')"
    )
    GRANTS: ClassVar[list[tuple[str, str, str, str]]] = [
        ("ana", "default.legacy", "unit", "1"),
        ("ana", "sales.orders", "unit", "1"),
        ("bo", "default.legacy", "unit", "2"),
        ("eve", "sales.orders", "unit", "3"),
    ]

    async def seeded(self, app: str) -> None:
        """Migrate to the version before the access schema, and give the two schemas their old grant tables."""
        await goose("console", app, "up-to", "21")
        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            await connection.execute(
                b"CREATE SCHEMA default__legacy; CREATE SCHEMA sales__orders"
            )
            for statement in self.SEED.decode().split(";"):
                await connection.execute(statement.encode())

    async def test_every_grant_with_a_unit_moves_to_its_scope_and_the_others_stay_behind(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """A row without a unit has no meaning in the new table, so the copy drops it, and the old table keeps it."""
        app, _ = scratch_databases
        await self.seeded(app)

        assert (await goose("console", app, "up")).returncode == 0

        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            moved = await (
                await connection.execute(
                    b"SELECT subject, scope::text, unit_type, unit_id FROM access.policy ORDER BY 1, 2"
                )
            ).fetchall()
            logged = await (
                await connection.execute(b"SELECT count(*) FROM access.log")
            ).fetchone()
            kept = await (
                await connection.execute(
                    b"SELECT count(*) FROM default__legacy.access_policy"
                )
            ).fetchone()

        assert moved == self.GRANTS
        assert logged == (0,)
        assert kept == (4,)

    async def test_the_down_part_drops_the_new_schema_and_keeps_the_old_tables(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """A rollback leaves the grants of the old tables as they were."""
        app, _ = scratch_databases
        await self.seeded(app)
        await goose("console", app, "up")

        assert (await goose("console", app, "down-to", "21")).returncode == 0

        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            gone = await (
                await connection.execute(b"SELECT to_regnamespace('access')")
            ).fetchone()
            old = await (
                await connection.execute(
                    b"SELECT count(*) FROM default__legacy.access_policy"
                )
            ).fetchone()

        assert gone == (None,)
        assert old == (4,)


@pytest.mark.postgres
@pytest.mark.skipif(GOOSE is None, reason="goose is not installed")
class TestFunctionNames:
    """The migration that drops the functions with the names of an older release."""

    async def test_the_old_names_go_and_the_others_stay(
        self, scratch_databases: tuple[str, str]
    ) -> None:
        """Drop `dl_changes_<table>` and `<table>_aggregate` in a schema of the console, and nothing else."""
        app, _ = scratch_databases
        await goose("console", app, "up-to", "22")
        async with await AsyncConnection.connect(
            scratch_url(app), autocommit=True
        ) as connection:
            await connection.execute(
                b"INSERT INTO console.schemas (project, short_name) VALUES ('default', 'x');"
            )
            for statement in (
                b"CREATE SCHEMA default__x",
                b"CREATE SCHEMA other",
                b"CREATE FUNCTION default__x.dl_changes_t(bigint, bigint) RETURNS int LANGUAGE sql AS 'SELECT 1'",
                b"CREATE FUNCTION default__x.t_aggregate() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                b"CREATE FUNCTION default__x.t_fn() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                b"CREATE FUNCTION other.t_aggregate() RETURNS int LANGUAGE sql AS 'SELECT 1'",
            ):
                await connection.execute(statement)

        assert (await goose("console", app, "up")).returncode == 0

        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            cursor = await connection.execute(
                b"SELECT n.nspname || '.' || p.proname FROM pg_proc AS p"
                b" JOIN pg_namespace AS n ON p.pronamespace = n.oid"
                b" WHERE n.nspname IN ('default__x', 'other') ORDER BY 1"
            )
            assert await cursor.fetchall() == [
                ("default__x.t_fn",),
                ("other.t_aggregate",),
            ]


class TestMigrationFiles:
    """The files of the migrations follow the rules that goose cannot check."""

    @pytest.mark.parametrize("folder", ["console", "ducklake"])
    def test_the_numbers_run_from_one_with_no_gap(self, folder: str) -> None:
        """A gap hides a file that a database at a higher version never applies."""
        numbers = [int(path.name[:5]) for path in migration_files(folder)]

        assert numbers == list(range(1, len(numbers) + 1))

    @pytest.mark.parametrize("folder", ["console", "ducklake"])
    def test_every_file_starts_with_one_short_description(self, folder: str) -> None:
        """The first comment paragraph says what the file changes, in at most eight lines."""
        for path in migration_files(folder):
            lines = description_lines(path.read_text())

            assert 1 <= len(lines) <= 8, (
                f"{path.name} has {len(lines)} description lines"
            )

    @pytest.mark.parametrize("folder", ["console", "ducklake"])
    def test_every_envsub_region_closes(self, folder: str) -> None:
        """A region that stays open changes the dollar signs of the statements after it."""
        for path in migration_files(folder):
            lines = [
                line.split()[-1]
                for line in path.read_text().split("\n")
                if line.startswith("-- +goose ENVSUB")
            ]
            assert lines == ["ON", "OFF"] * (len(lines) // 2), path.name

    @pytest.mark.parametrize("folder", ["console", "ducklake"])
    def test_a_name_from_the_environment_is_quoted(self, folder: str) -> None:
        """A name that is not set becomes an empty quoted name, which PostgreSQL refuses."""
        for path in migration_files(folder):
            for line in path.read_text().split("\n"):
                if "${" in line and not line.startswith("--"):
                    assert '"${' in line, f"{path.name}: {line}"

    def test_the_grants_file_fills_a_name_in_every_grant(self) -> None:
        """The grants use the names of the roles of the run."""
        sql = migration_sql("console", "00014_grants.sql", env=ROLES)

        assert "TO dp_api" not in sql
        assert 'TO "dp_api"' in sql
        assert 'TO "anon"' in sql


OLD_FUNCTIONS = (
    "CREATE FUNCTION default__sales.dl_latest_snapshot() RETURNS bigint LANGUAGE sql AS 'SELECT 1'",
    "CREATE FUNCTION default__sales.dl_table_files(text) RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "CREATE FUNCTION default__sales.dl_chg(text, bigint, bigint) RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "CREATE FUNCTION default__sales.dl_chg(text) RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "CREATE FUNCTION default__other.dl_chg(text, bigint, bigint) RETURNS int LANGUAGE sql AS 'SELECT 1'",
)
WHICH_FUNCTIONS = (
    "SELECT string_agg(n.nspname || '.' || p.proname || '/' || p.pronargs, ' ' ORDER BY n.nspname, p.proname, p.pronargs) "
    "FROM pg_proc AS p INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid "
    "WHERE n.nspname IN ('default__sales', 'default__other') AND p.proname LIKE 'dl\\_%'"
)
WHOLE_GRANTEES = "SELECT has_function_privilege('dp_api', 'rbac.whole(text)', 'EXECUTE'), to_regprocedure('rbac.all_rows(text)') IS NULL"


@pytest.mark.postgres
@pytest.mark.skipif(GOOSE is None, reason="goose is not installed")
class TestRenameFunctionsMigration:
    """Migration 25 drops the old names of the data schemas and renames the rbac function, over a database at version 24."""

    @pytest.fixture
    async def at_24(self, scratch_databases: tuple[str, str]) -> str:
        """Bring the database to version 24, with the old functions in one console schema, a look-alike in another, and a grant on rbac.all_rows."""
        app, _ = scratch_databases
        assert (await goose("console", app, "up-to", "24")).returncode == 0
        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            await connection.execute(
                b"INSERT INTO console.schemas (name) VALUES ('default__sales')"
            )
            await connection.execute(b"CREATE SCHEMA IF NOT EXISTS default__sales")
            await connection.execute(b"CREATE SCHEMA IF NOT EXISTS default__other")
            await connection.execute(
                b"GRANT EXECUTE ON FUNCTION rbac.all_rows(text) TO dp_api"
            )
            for statement in OLD_FUNCTIONS:
                await connection.execute(statement.encode())
            await connection.commit()
        return app

    async def read(self, app: str, sql: str) -> tuple[object, ...]:
        """Run one query on the scratch database and return its row."""
        async with await AsyncConnection.connect(scratch_url(app)) as connection:
            row = await (await connection.execute(sql.encode())).fetchone()
        assert row is not None
        return row

    async def test_up_drops_the_three_old_functions_and_keeps_the_others(
        self, at_24: str
    ) -> None:
        """Only the three names with their arguments in a console schema go. A function with the same name and other arguments, or in another schema, stays."""
        assert (await goose("console", at_24, "up")).returncode == 0

        assert await self.read(at_24, WHICH_FUNCTIONS) == (
            "default__other.dl_chg/3 default__sales.dl_chg/1",
        )

    async def test_up_renames_the_rbac_function_and_keeps_its_grant(
        self, at_24: str
    ) -> None:
        """The request role runs rbac.whole, and rbac.all_rows is gone."""
        await goose("console", at_24, "up")

        assert await self.read(at_24, WHOLE_GRANTEES) == (True, True)

    async def test_up_points_the_console_function_at_the_new_name(
        self, at_24: str
    ) -> None:
        """console.table_files calls files and no longer dl_table_files."""
        await goose("console", at_24, "up")

        (definition,) = await self.read(
            at_24,
            "SELECT pg_get_functiondef('console.table_files(text, text)'::regprocedure)",
        )

        assert ".files($1)" in str(definition)
        assert "dl_table_files" not in str(definition)

    async def test_down_then_up_restores_the_old_name_and_applies_again(
        self, at_24: str
    ) -> None:
        """The Down part brings back rbac.all_rows and the old call, and the Up part works once more."""
        await goose("console", at_24, "up")

        assert (await goose("console", at_24, "down")).returncode == 0
        assert await self.read(
            at_24, "SELECT to_regprocedure('rbac.all_rows(text)') IS NOT NULL"
        ) == (True,)
        assert (await goose("console", at_24, "up")).returncode == 0
        assert await self.read(at_24, WHOLE_GRANTEES) == (True, True)

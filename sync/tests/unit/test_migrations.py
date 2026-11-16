import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Mapping
from uuid import uuid4

import psycopg
import pytest
from dbos._migration import ensure_dbos_schema, run_dbos_migrations
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier
from sqlalchemy import create_engine

from tests.constants import POSTGRES_URL
from tests.fixtures.types import Postgres
from tests.helpers.core import scalar
from tests.helpers.migrations import (
    MIGRATIONS,
    ROLES,
    description_lines,
    migration_files,
    migration_sql,
)

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


GOOSE = shutil.which("goose")
CONSOLE_WRITES = {"projects", "rls", "schemas", "tables"}


async def relations_with(pg: Postgres, privilege: str, *, any_column: bool) -> set[str]:
    """Return the console tables on which the role of the tokens holds a privilege, on a column or on the table."""
    check = "has_any_column_privilege" if any_column else "has_table_privilege"
    cursor = await pg.connection.execute(
        f"""
        SELECT c.relname
        FROM pg_class AS c
        INNER JOIN pg_namespace AS n ON c.relnamespace = n.oid
        WHERE n.nspname = 'console' AND c.relkind IN ('r', 'p')
            AND {check}('dp_api', c.oid, %s)
        """.encode(),
        (privilege,),
    )

    return {name for (name,) in await cursor.fetchall()}


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


def scratch_url(name: str) -> str:
    """Return the connection string of a scratch database."""
    return f"{POSTGRES_URL}/{name}"


async def goose(
    folder: str,
    name: str,
    *command: str,
    env: Mapping[str, str] = ROLES,
) -> subprocess.CompletedProcess[str]:
    """Run goose on a scratch database, with the role names that the migrations fill into their grants."""
    return await asyncio.to_thread(
        subprocess.run,
        [
            str(GOOSE),
            "-dir",
            str(MIGRATIONS / folder),
            "postgres",
            scratch_url(name),
            *command,
        ],
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("AUTH_")},
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
async def scratch_databases() -> AsyncIterator[tuple[str, str]]:
    """Give two empty databases, with the DBOS tables in the first, for a run of the whole set of migrations."""
    app, catalog = f"mig_app_{uuid4().hex[:8]}", f"mig_cat_{uuid4().hex[:8]}"
    async with await AsyncConnection.connect(
        f"{POSTGRES_URL}/postgres", autocommit=True
    ) as admin:
        for name in (app, catalog):
            await admin.execute(SQL("CREATE DATABASE {}").format(Identifier(name)))

        engine = create_engine(
            scratch_url(app).replace("postgresql://", "postgresql+psycopg://", 1)
        )
        try:
            ensure_dbos_schema(engine, "dbos")
            run_dbos_migrations(engine, "dbos", use_listen_notify=True)
            yield app, catalog
        finally:
            engine.dispose()
            for name in (app, catalog):
                await admin.execute(
                    SQL("DROP DATABASE {} WITH (FORCE)").format(Identifier(name))
                )


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

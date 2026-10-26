import pytest

from tests.fixtures.console import add_schema, add_table, as_token, refusal
from tests.fixtures.types import Postgres
from tests.helpers import scalar

INSUFFICIENT = "42501"
NOT_FOUND = "PT404"
CONFLICT = "PT409"
BAD_REQUEST = "PT400"


async def values(pg: Postgres, sql: str, *params: object) -> list[object]:
    """Return the first column of every row of a statement."""
    cursor = await pg.connection.execute(sql.encode(), params)

    return [row[0] for row in await cursor.fetchall()]


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


@pytest.mark.postgres
class TestPermissions:
    """console.permissions() gives the permissions of the role of the token."""

    @pytest.mark.parametrize(
        ("role", "has", "lacks"),
        [
            pytest.param("dp_api", ["read_api"], ["read_sync", "start_sync"], id="api"),
            pytest.param(
                "dp_console",
                ["read_api", "read_sync", "read_config"],
                ["start_sync", "edit_config"],
                id="console",
            ),
            pytest.param(
                "dp_policy",
                ["read_policy", "create_policy"],
                ["read_sync"],
                id="policy",
            ),
            pytest.param(
                "dp_admin",
                ["start_sync", "control_sync", "import_config", "read_files"],
                [],
                id="admin",
            ),
        ],
    )
    async def test_lists_what_the_role_may_do(
        self, console_api: Postgres, role: str, has: list[str], lacks: list[str]
    ) -> None:
        """Show the console the actions that it may offer."""
        await as_token(console_api, role, ["sales"])

        granted = await scalar(console_api, "SELECT console.permissions()")

        assert isinstance(granted, list)
        assert set(has) <= set(granted)
        assert set(lacks).isdisjoint(granted)

    async def test_lists_nothing_for_a_request_with_no_token(
        self, console_api: Postgres
    ) -> None:
        """Give an anonymous request no permission."""
        assert await scalar(console_api, "SELECT console.permissions()") == []


@pytest.mark.postgres
class TestViewsAreScopedBySchema:
    """The views and the tables of the console show a token only its schemas."""

    async def test_runs_are_the_runs_of_the_schemas_of_the_token(
        self, console_api: Postgres
    ) -> None:
        """Hide a run of another schema, and show the schemas of the run that the token reaches."""
        await two_schemas(console_api)
        await start(console_api, ["sales"])
        await start(console_api, ["hr"])
        await as_token(console_api, "dp_console", ["sales"], database_role=True)

        rows = await values(console_api, "SELECT schemas FROM console.runs")

        assert rows == [["sales"]]

    async def test_a_run_shows_only_the_schemas_that_the_token_reaches(
        self, console_api: Postgres
    ) -> None:
        """Do not tell a user the other schemas of a run that covers several."""
        await two_schemas(console_api)
        await start(console_api, ["sales", "hr"])
        await as_token(console_api, "dp_console", ["hr"], database_role=True)

        assert await values(console_api, "SELECT schemas FROM console.runs") == [["hr"]]

    async def test_no_run_is_visible_without_the_permission_to_read_them(
        self, console_api: Postgres
    ) -> None:
        """A client of the data API sees no run, even of its own schema."""
        await two_schemas(console_api)
        await start(console_api, ["sales"])
        await as_token(console_api, "dp_api", ["sales"], database_role=True)

        assert await values(console_api, "SELECT id FROM console.runs") == []

    async def test_errors_belong_to_the_schema_of_their_fields_or_of_their_table(
        self, console_api: Postgres
    ) -> None:
        """Place an error by its schema field, or by the schema of the table that it names."""
        await two_schemas(console_api)
        await console_api.connection.execute(
            """
            INSERT INTO data_proxy.errors (reason, fields) VALUES
                ('in sales by schema', '{"schema": "sales"}'),
                ('in sales by table', '{"table": "p.d.orders"}'),
                ('in hr by table', '{"table": "p.d.staff"}'),
                ('nowhere', '{}')
            """
        )
        await as_token(console_api, "dp_console", ["sales"], database_role=True)

        reasons = await values(
            console_api, "SELECT reason FROM console.errors ORDER BY reason"
        )

        assert reasons == ["in sales by schema", "in sales by table"]

    @pytest.mark.parametrize(
        ("role", "schemas", "expected"),
        [
            pytest.param(
                "dp_console", ["sales"], ["sales"], id="console-of-one-schema"
            ),
            pytest.param(
                "dp_admin", ["sales", "hr"], ["hr", "sales"], id="admin-of-both"
            ),
            pytest.param("dp_console", ["other"], [], id="console-of-another-schema"),
            pytest.param("dp_api", ["sales"], [], id="no-config-permission"),
            pytest.param("dp_admin", ["*"], ["hr", "sales"], id="every-schema"),
        ],
    )
    async def test_the_configuration_is_the_one_of_the_schemas_of_the_token(
        self,
        console_api: Postgres,
        role: str,
        schemas: list[str],
        expected: list[str],
    ) -> None:
        """Show the schemas, and with them the tables, to a token that reads the configuration."""
        await two_schemas(console_api)
        await as_token(console_api, role, schemas, database_role=True)

        assert (
            await values(console_api, "SELECT name FROM console.schemas ORDER BY name")
            == expected
        )
        assert (
            await values(
                console_api, "SELECT DISTINCT schema FROM console.tables ORDER BY 1"
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("role", "schemas", "refused"),
        [
            pytest.param("dp_console", ["sales"], True, id="no-create-permission"),
            pytest.param("dp_admin", ["hr"], True, id="schema-outside-the-claim"),
            pytest.param("dp_admin", ["sales"], False, id="schema-of-the-claim"),
        ],
    )
    async def test_a_token_adds_a_schema_only_inside_its_claim(
        self, console_api: Postgres, role: str, schemas: list[str], refused: bool
    ) -> None:
        """Let the row policy decide who creates a schema."""
        await as_token(console_api, role, schemas, database_role=True)

        state = await refusal(
            console_api, "INSERT INTO console.schemas (name) VALUES ('sales')"
        )

        assert state == (INSUFFICIENT if refused else None)

    async def test_health_tells_a_schema_without_its_access_policy_from_one_that_has_it(
        self, console_api: Postgres
    ) -> None:
        """Say that a schema is provisioned only when its schema and its access policy exist."""
        await add_schema(console_api, "sales", claim="cpf")
        await add_schema(console_api, "hr", claim="cpf", provisioned=False)
        await as_token(console_api, "dp_admin", ["sales", "hr"], database_role=True)

        cursor = await console_api.connection.execute(
            b"SELECT name, schema_exists, access_policy FROM console.health ORDER BY name"
        )

        assert await cursor.fetchall() == [("hr", False, False), ("sales", True, True)]


@pytest.mark.postgres
class TestStartSync:
    """console.start_sync() and console.start_scope() check the token, and then queue the run."""

    async def test_queues_one_run_with_its_scope_and_the_user_name(
        self, console_api: Postgres
    ) -> None:
        """Queue run_sync for the schemas that the user asked for."""
        await two_schemas(console_api)
        await as_token(console_api, "dp_admin", ["sales", "hr"], user="lia")

        run = await scalar(console_api, "SELECT console.start_sync(%s, %s)", ["hr"], [])

        cursor = await console_api.connection.execute(
            b"SELECT name, queue_name, status, authenticated_user FROM dbos.workflow_status WHERE workflow_uuid = %s",
            (run,),
        )
        assert await cursor.fetchall() == [("run_sync", "sync", "ENQUEUED", "lia")]
        assert await values(
            console_api,
            "SELECT schema_name FROM data_proxy.scope WHERE run_id = %s",
            run,
        ) == ["hr"]

    async def test_a_run_with_no_scope_covers_every_schema_of_the_token(
        self, console_api: Postgres
    ) -> None:
        """Default to the schemas that the token reaches, in order."""
        await two_schemas(console_api)
        await as_token(console_api, "dp_admin", ["sales", "hr"])

        scope = await scalar(console_api, "SELECT console.start_scope('{}', '{}')")

        assert scope == ["hr", "sales"]

    async def test_tables_alone_give_the_schemas_that_they_belong_to(
        self, console_api: Postgres
    ) -> None:
        """Start the schemas of the tables that the user chose."""
        await two_schemas(console_api)
        await as_token(console_api, "dp_admin", ["sales", "hr"])

        scope = await scalar(
            console_api, "SELECT console.start_scope('{}', %s)", ["p.d.staff"]
        )

        assert scope == ["hr"]

    @pytest.mark.parametrize(
        ("role", "schemas", "asked", "tables"),
        [
            pytest.param(
                "dp_console", ["sales"], ["sales"], [], id="no-start-permission"
            ),
            pytest.param(
                "dp_admin", ["sales"], ["hr"], [], id="schema-outside-the-claim"
            ),
            pytest.param(
                "dp_admin", ["sales"], [], ["p.d.staff"], id="table-outside-the-claim"
            ),
            pytest.param(
                "dp_admin",
                ["sales"],
                [],
                ["p.d.nothing"],
                id="table-that-does-not-exist",
            ),
            pytest.param(
                "dp_admin",
                ["other"],
                [],
                [],
                id="a-claim-with-no-schema-of-the-console",
            ),
        ],
    )
    async def test_refuses_what_the_token_does_not_reach(
        self,
        console_api: Postgres,
        role: str,
        schemas: list[str],
        asked: list[str],
        tables: list[str],
    ) -> None:
        """Answer insufficient privilege, and queue nothing."""
        await two_schemas(console_api)
        await as_token(console_api, role, schemas)
        before = await scalar(console_api, "SELECT count(*) FROM dbos.workflow_status")

        state = await refusal(
            console_api, "SELECT console.start_sync(%s, %s)", asked, tables
        )

        assert state == INSUFFICIENT
        assert (
            await scalar(console_api, "SELECT count(*) FROM dbos.workflow_status")
            == before
        )

    async def test_refuses_a_schema_that_the_provision_job_has_not_made(
        self, console_api: Postgres
    ) -> None:
        """Tell the user to wait, with a conflict, for a schema with no access policy yet."""
        await add_schema(console_api, "sales", claim="cpf", provisioned=False)
        await as_token(console_api, "dp_admin", ["sales"])

        state = await refusal(console_api, "SELECT console.start_sync('{}', '{}')")

        assert state == CONFLICT

    async def test_refuses_a_schema_that_is_not_in_the_console(
        self, console_api: Postgres
    ) -> None:
        """Say that the schema is unknown, for a request that names one that the console lacks."""
        await as_token(console_api, "dp_admin", ["*"])

        state = await refusal(
            console_api, "SELECT console.start_sync(%s, '{}')", ["ghost"]
        )

        assert state == BAD_REQUEST


async def set_status(pg: Postgres, run: str, status: str) -> None:
    """Move a run to a status, as the workers do."""
    await pg.connection.execute(
        "UPDATE dbos.workflow_status SET status = %s WHERE workflow_uuid = %s",
        (status, run),
    )


@pytest.mark.postgres
class TestRunControl:
    """Cancel, resume and run again follow the status of the run and the schemas of the token."""

    @pytest.mark.parametrize(
        ("function", "status", "queued"),
        [
            pytest.param(
                "cancel_run", "ENQUEUED", "cancel_run", id="cancel-a-queued-run"
            ),
            pytest.param(
                "cancel_run", "PENDING", "cancel_run", id="cancel-a-running-run"
            ),
            pytest.param(
                "resume_run", "CANCELLED", "resume_run", id="resume-a-cancelled-run"
            ),
            pytest.param(
                "resume_run",
                "MAX_RECOVERY_ATTEMPTS_EXCEEDED",
                "resume_run",
                id="resume-a-run-that-gave-up",
            ),
        ],
    )
    async def test_queues_the_control_workflow_for_a_run_in_the_right_status(
        self, console_api: Postgres, function: str, status: str, queued: str
    ) -> None:
        """Hand the action to the control queue, with the run id as its argument."""
        await two_schemas(console_api)
        run = await start(console_api, ["sales"])
        await set_status(console_api, run, status)

        await console_api.connection.execute(
            f"SELECT console.{function}(%s)".encode(), (run,)
        )

        cursor = await console_api.connection.execute(
            b"SELECT queue_name, authenticated_user FROM dbos.workflow_status WHERE name = %s",
            (queued,),
        )
        assert await cursor.fetchall() == [("control", "ana")]

    @pytest.mark.parametrize(
        ("function", "status"),
        [
            pytest.param("cancel_run", "SUCCESS", id="cancel-a-finished-run"),
            pytest.param("cancel_run", "CANCELLED", id="cancel-a-cancelled-run"),
            pytest.param("resume_run", "ENQUEUED", id="resume-a-queued-run"),
            pytest.param("resume_run", "SUCCESS", id="resume-a-finished-run"),
        ],
    )
    async def test_answers_a_conflict_for_a_run_in_the_wrong_status(
        self, console_api: Postgres, function: str, status: str
    ) -> None:
        """Refuse an action that the status of the run does not allow."""
        await two_schemas(console_api)
        run = await start(console_api, ["sales"])
        await set_status(console_api, run, status)

        assert (
            await refusal(console_api, f"SELECT console.{function}(%s)", run)
            == CONFLICT
        )

    @pytest.mark.parametrize(
        ("role", "schemas", "expected"),
        [
            pytest.param(
                "dp_console", ["sales"], INSUFFICIENT, id="no-control-permission"
            ),
            pytest.param("dp_admin", ["hr"], NOT_FOUND, id="run-of-another-schema"),
            pytest.param("dp_admin", ["sales"], None, id="run-of-the-schema"),
        ],
    )
    async def test_the_token_controls_only_the_runs_of_its_schemas(
        self,
        console_api: Postgres,
        role: str,
        schemas: list[str],
        expected: str | None,
    ) -> None:
        """Answer not found for a run that the token cannot see, and insufficient privilege without the right."""
        await two_schemas(console_api)
        run = await start(console_api, ["sales"])
        await as_token(console_api, role, schemas)

        assert (
            await refusal(console_api, "SELECT console.cancel_run(%s)", run) == expected
        )

    async def test_answers_not_found_for_a_run_that_does_not_exist(
        self, console_api: Postgres
    ) -> None:
        """Do not tell a missing run from a run that the token cannot see."""
        await two_schemas(console_api)
        await as_token(console_api, "dp_admin", ["sales"])

        assert (
            await refusal(console_api, "SELECT console.cancel_run('ghost')")
            == NOT_FOUND
        )

    async def test_run_again_starts_a_new_run_for_the_schemas_of_the_old_one(
        self, console_api: Postgres
    ) -> None:
        """Copy the scope of a finished run to a new run."""
        await two_schemas(console_api)
        run = await start(console_api, ["sales", "hr"])
        await set_status(console_api, run, "ERROR")

        again = await scalar(console_api, "SELECT console.retry_run(%s)", run)

        assert again != run
        assert await values(
            console_api,
            "SELECT schema_name FROM data_proxy.scope WHERE run_id = %s ORDER BY 1",
            again,
        ) == ["hr", "sales"]

    async def test_run_again_keeps_only_the_schemas_that_the_token_reaches(
        self, console_api: Postgres
    ) -> None:
        """Do not widen the scope of a run for a user who sees part of it."""
        await two_schemas(console_api)
        run = await start(console_api, ["sales", "hr"])
        await set_status(console_api, run, "SUCCESS")
        await as_token(console_api, "dp_admin", ["sales"])

        again = await scalar(console_api, "SELECT console.retry_run(%s)", run)

        assert await values(
            console_api,
            "SELECT schema_name FROM data_proxy.scope WHERE run_id = %s",
            again,
        ) == ["sales"]

    async def test_run_again_needs_a_run_that_has_finished(
        self, console_api: Postgres
    ) -> None:
        """Refuse to start a second copy of a run that is still queued."""
        await two_schemas(console_api)
        run = await start(console_api, ["sales"])

        assert (
            await refusal(console_api, "SELECT console.retry_run(%s)", run) == CONFLICT
        )


async def add_schedule(pg: Postgres, name: str) -> None:
    """Add a schedule as DBOS stores it."""
    await pg.connection.execute(
        "INSERT INTO dbos.workflow_schedules (schedule_id, schedule_name, workflow_name, schedule, context) "
        "VALUES (%s, %s, 'run_sync', '0 2 * * *', '{}')",
        (f"id-{name}", name),
    )


@pytest.mark.postgres
class TestScheduleControl:
    """A schedule serves every schema, so only a token for every schema controls it."""

    @pytest.mark.parametrize(
        ("function", "action"),
        [("pause_schedule", "pause_schedule"), ("resume_schedule", "resume_schedule")],
    )
    async def test_queues_the_action_for_a_token_that_reaches_every_schema(
        self, console_api: Postgres, function: str, action: str
    ) -> None:
        """Hand the action to the control queue."""
        await two_schemas(console_api)
        await add_schedule(console_api, "sync")
        await as_token(console_api, "dp_admin", ["*"])

        await console_api.connection.execute(
            f"SELECT console.{function}('sync')".encode()
        )

        assert await values(
            console_api,
            "SELECT queue_name FROM dbos.workflow_status WHERE name = %s",
            action,
        ) == ["control"]

    @pytest.mark.parametrize(
        ("role", "schemas", "expected"),
        [
            pytest.param("dp_admin", ["sales", "hr"], None, id="every-schema-by-name"),
            pytest.param("dp_admin", ["*"], None, id="every-schema-by-star"),
            pytest.param("dp_admin", ["sales"], INSUFFICIENT, id="one-schema-of-two"),
            pytest.param("dp_console", ["*"], INSUFFICIENT, id="no-control-permission"),
        ],
    )
    async def test_needs_every_schema_and_the_permission_to_control(
        self,
        console_api: Postgres,
        role: str,
        schemas: list[str],
        expected: str | None,
    ) -> None:
        """Refuse a token with a narrower claim, or without control_sync."""
        await two_schemas(console_api)
        await add_schedule(console_api, "sync")
        await as_token(console_api, role, schemas)

        assert (
            await refusal(console_api, "SELECT console.pause_schedule('sync')")
            == expected
        )

    async def test_answers_not_found_for_a_schedule_that_does_not_exist(
        self, console_api: Postgres
    ) -> None:
        """Name no schedule that DBOS lacks."""
        await as_token(console_api, "dp_admin", ["*"])

        assert (
            await refusal(console_api, "SELECT console.pause_schedule('ghost')")
            == NOT_FOUND
        )


async def add_files_function(pg: Postgres, schema: str) -> None:
    """Add a stand-in for the function that lists the files of a table, which the sync worker makes."""
    await pg.connection.execute(
        f"""
        CREATE FUNCTION {schema}.dl_table_files(table_name text)
        RETURNS TABLE (path text, size_bytes bigint, delete_file text)
        LANGUAGE sql AS $$ SELECT table_name || '.parquet', 10::bigint, NULL::text $$
        """.encode()
    )


@pytest.mark.postgres
class TestTableFiles:
    """console.table_files() lists the files of one table for a token that may read them."""

    async def test_lists_the_files_of_a_table_by_its_short_name(
        self, console_api: Postgres
    ) -> None:
        """Pass the part of the table name after the last dot to the function of the schema."""
        await two_schemas(console_api)
        await add_files_function(console_api, "sales")
        await as_token(console_api, "dp_admin", ["sales"])

        paths = await values(
            console_api, "SELECT path FROM console.table_files('sales', 'p.d.orders')"
        )

        assert paths == ["orders.parquet"]

    @pytest.mark.parametrize(
        ("role", "schemas", "schema", "table"),
        [
            pytest.param(
                "dp_console", ["sales"], "sales", "p.d.orders", id="no-files-permission"
            ),
            pytest.param(
                "dp_admin", ["hr"], "sales", "p.d.orders", id="schema-outside-the-claim"
            ),
            pytest.param(
                "dp_admin",
                ["sales"],
                "sales",
                "p.d.staff",
                id="table-of-another-schema",
            ),
            pytest.param(
                "dp_admin",
                ["sales"],
                "sales",
                "p.d.ghost",
                id="table-that-does-not-exist",
            ),
        ],
    )
    async def test_refuses_what_the_token_does_not_reach(
        self,
        console_api: Postgres,
        role: str,
        schemas: list[str],
        schema: str,
        table: str,
    ) -> None:
        """Answer insufficient privilege, with one answer for a table that is not there and one that is forbidden."""
        await two_schemas(console_api)
        await add_files_function(console_api, "sales")
        await as_token(console_api, role, schemas)

        state = await refusal(
            console_api, "SELECT * FROM console.table_files(%s, %s)", schema, table
        )

        assert state == INSUFFICIENT

    async def test_answers_a_conflict_for_an_encrypted_schema(
        self, console_api: Postgres
    ) -> None:
        """Say that the files of an encrypted schema cannot be read in a notebook."""
        await two_schemas(console_api)
        await add_files_function(console_api, "sales")
        await console_api.connection.execute(
            "UPDATE console.schemas SET encrypted = TRUE WHERE name = 'sales'"
        )
        await as_token(console_api, "dp_admin", ["sales"])

        state = await refusal(
            console_api, "SELECT * FROM console.table_files('sales', 'p.d.orders')"
        )

        assert state == CONFLICT

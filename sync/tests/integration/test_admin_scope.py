import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.sql import SQL, Literal

from tests.helpers import become, rows

SEED = """
INSERT INTO admin.schemas (name, claim) VALUES ('alpha', 'sub'), ('beta', 'sub');
INSERT INTO admin.tables (name, schema) VALUES ('p.d.a1', 'alpha'), ('p.d.b1', 'beta');
INSERT INTO admin.rls (table_name, column_name, unit_type)
VALUES ('p.d.a1', 'c', 'u'), ('p.d.b1', 'c', 'u');
INSERT INTO dbos.workflow_status (
    workflow_uuid, status, name, queue_name, queue_partition_key,
    parent_workflow_id, created_at
) VALUES
    ('run-1', 'SUCCESS', 'run_sync', 'sync', NULL, NULL, (extract(epoch FROM now()) * 1000)::bigint),
    ('run-2', 'ERROR', 'run_sync', 'sync', NULL, NULL, (extract(epoch FROM now()) * 1000)::bigint + 1000),
    ('publish-alpha', 'SUCCESS', 'publish_schema', 'publish', 'alpha', 'run-1', 0),
    ('publish-beta', 'SUCCESS', 'publish_schema', 'publish', 'beta', 'run-1', 0),
    ('dump-a', 'SUCCESS', 'dump_task', 'dump', NULL, 'run-1', 0),
    ('dump-b', 'SUCCESS', 'dump_task', 'dump', NULL, 'run-1', 0),
    ('dump-loose', 'SUCCESS', 'dump_task', 'dump', NULL, NULL, 0);
INSERT INTO data_proxy.scope (workflow_id, run_id, schema_name, table_name) VALUES
    ('run-1', 'run-1', 'alpha', ''),
    ('run-1', 'run-1', 'beta', ''),
    ('run-2', 'run-2', 'beta', ''),
    ('dump-a', 'run-1', 'alpha', 'p.d.a1'),
    ('dump-b', 'run-1', 'beta', 'p.d.b1');
INSERT INTO dbos.operation_outputs (workflow_uuid, function_id, function_name) VALUES
    ('run-1', 0, 'seed_schemas'),
    ('run-2', 0, 'seed_schemas'),
    ('dump-a', 0, 'extract'),
    ('dump-b', 0, 'extract'),
    ('publish-alpha', 0, 'publish'),
    ('publish-beta', 0, 'publish');
INSERT INTO dbos.streams (workflow_uuid, key, value, "offset", serialization) VALUES
    ('publish-alpha', 'logs', '{"time": "2023-11-14T22:13:20+00:00", "level": "INFO", "message": "a", "fields": {}, "schema": "alpha", "table": null}', 0, 'portable_json'),
    ('publish-beta', 'logs', '{"time": "2023-11-14T22:13:20+00:00", "level": "INFO", "message": "b", "fields": {}, "schema": "beta", "table": null}', 0, 'portable_json'),
    ('dump-loose', 'logs', '{"time": "2023-11-14T22:13:20+00:00", "level": "INFO", "message": "c", "fields": {}, "schema": null, "table": null}', 0, 'portable_json');
INSERT INTO data_proxy.errors (reason, fields)
VALUES ('table_blocked', '{"table": "p.d.a1"}'), ('table_blocked', '{"table": "p.d.b1"}');
INSERT INTO data_proxy.state (table_name, state)
VALUES ('p.d.a1', '{"signature": "s"}'), ('p.d.b1', '{"signature": "s"}');
"""

# For each query: the rows of a user of alpha, of a user of alpha and beta, and of the admin.
# A task without a scope row or a schema, and a log line without a schema, are for the admin only.
QUERIES = [
    pytest.param(
        "SELECT name FROM admin.schemas",
        ["alpha"],
        ["alpha", "beta"],
        ["alpha", "beta"],
        id="schemas",
    ),
    pytest.param(
        "SELECT name FROM admin.tables",
        ["p.d.a1"],
        ["p.d.a1", "p.d.b1"],
        ["p.d.a1", "p.d.b1"],
        id="tables",
    ),
    pytest.param(
        "SELECT table_name FROM admin.rls",
        ["p.d.a1"],
        ["p.d.a1", "p.d.b1"],
        ["p.d.a1", "p.d.b1"],
        id="rules",
    ),
    pytest.param(
        "SELECT name FROM admin.health",
        ["alpha"],
        ["alpha", "beta"],
        ["alpha", "beta"],
        id="health",
    ),
    pytest.param(
        "SELECT name FROM admin.synced",
        ["p.d.a1"],
        ["p.d.a1", "p.d.b1"],
        ["p.d.a1", "p.d.b1"],
        id="synced",
    ),
    pytest.param(
        "SELECT id FROM admin.tasks WHERE id LIKE 'publish-%' OR id LIKE 'dump-%'",
        ["dump-a", "publish-alpha"],
        ["dump-a", "dump-b", "publish-alpha", "publish-beta"],
        ["dump-a", "dump-b", "dump-loose", "publish-alpha", "publish-beta"],
        id="tasks",
    ),
    pytest.param(
        "SELECT workflow_id FROM admin.logs WHERE workflow_id LIKE 'publish-%' OR workflow_id LIKE 'dump-%'",
        ["publish-alpha"],
        ["publish-alpha", "publish-beta"],
        ["dump-loose", "publish-alpha", "publish-beta"],
        id="logs",
    ),
    pytest.param(
        "SELECT fields ->> 'table' FROM admin.errors WHERE reason = 'table_blocked'",
        ["p.d.a1"],
        ["p.d.a1", "p.d.b1"],
        ["p.d.a1", "p.d.b1"],
        id="errors",
    ),
    pytest.param(
        "SELECT id FROM admin.runs WHERE id LIKE 'run-%'",
        ["run-1"],
        ["run-1", "run-2"],
        ["run-1", "run-2"],
        id="runs",
    ),
    pytest.param(
        "SELECT id || ':' || task_count || ':' || failed_task_count FROM admin.runs WHERE id LIKE 'run-%'",
        ["run-1:2:0"],
        ["run-1:4:0", "run-2:0:0"],
        ["run-1:4:0", "run-2:0:0"],
        id="run-task-counts",
    ),
    pytest.param(
        "SELECT DISTINCT workflow_id FROM admin.steps WHERE workflow_id LIKE 'run-%' OR workflow_id LIKE 'dump-%' OR workflow_id LIKE 'publish-%'",
        ["dump-a", "publish-alpha", "run-1"],
        ["dump-a", "dump-b", "publish-alpha", "publish-beta", "run-1", "run-2"],
        ["dump-a", "dump-b", "publish-alpha", "publish-beta", "run-1", "run-2"],
        id="steps",
    ),
    pytest.param(
        "SELECT concat_ws(':', name, tables, synced_tables, last_run_id, runs_24h, failed_runs_24h, errors_24h) FROM admin.overview",
        ["alpha:1:1:run-1:1:0:1"],
        ["alpha:1:1:run-1:1:0:1", "beta:1:1:run-2:2:1:1"],
        ["alpha:1:1:run-1:1:0:1", "beta:1:1:run-2:2:1:1"],
        id="overview",
    ),
    pytest.param(
        "SELECT jsonb_object_keys(admin.sync_config() -> 'schemas')",
        ["alpha"],
        ["alpha", "beta"],
        ["alpha", "beta"],
        id="configuration-document",
    ),
]


async def as_user(
    connection: psycopg.AsyncConnection, role: str, schemas: str | None
) -> None:
    """Act as PostgREST does for a token: set the claim of the schemas, then switch the role."""
    if schemas is not None:
        await connection.execute(
            b"SELECT set_config('app.claim_schemas', %s, false)", (schemas,)
        )
    await become(connection, role)


async def names(connection: psycopg.AsyncConnection, query: str) -> list[str]:
    """Run one query and return the first column, sorted."""
    return sorted(str(row[0]) for row in await rows(connection, query))


@pytest.fixture
async def seeded(admin_db: psycopg.AsyncConnection) -> psycopg.AsyncConnection:
    """Provide a connection with two schemas, each with a table, a task, a log line, and an error."""
    await admin_db.execute(SEED.encode())
    return admin_db


@pytest.mark.postgres
class TestScopedReads:
    """A level sees the schemas of its token, and the admin sees all of them."""

    @pytest.mark.parametrize(("query", "scoped", "both", "everything"), QUERIES)
    @pytest.mark.parametrize(
        ("role", "schemas", "sees"),
        [
            pytest.param("console_viewer", "alpha", "scoped", id="viewer-of-alpha"),
            pytest.param("console_operator", "alpha", "scoped", id="operator-of-alpha"),
            pytest.param("console_editor", "alpha", "scoped", id="editor-of-alpha"),
            pytest.param("console_viewer", "alpha,beta", "both", id="viewer-of-both"),
            pytest.param(
                "console_viewer", None, "nothing", id="viewer-without-schemas"
            ),
            pytest.param(
                "console_viewer", "", "nothing", id="viewer-with-empty-schemas"
            ),
            pytest.param("console_admin", "alpha", "everything", id="admin-of-alpha"),
            pytest.param(
                "console_admin", None, "everything", id="admin-without-schemas"
            ),
        ],
    )
    async def test_a_user_sees_what_its_schemas_allow(
        self,
        seeded: psycopg.AsyncConnection,
        query: str,
        scoped: list[str],
        both: list[str],
        everything: list[str],
        role: str,
        schemas: str | None,
        sees: str,
    ) -> None:
        """The database filters every read, so no page can show another schema."""
        nothing: list[str] = []
        wanted = {
            "nothing": nothing,
            "scoped": scoped,
            "both": both,
            "everything": everything,
        }[sees]
        await as_user(seeded, role, schemas)

        assert await names(seeded, query) == wanted


@pytest.mark.postgres
class TestScopedWrites:
    """An editor changes the schemas of its token, and the admin changes all of them."""

    async def test_an_editor_updates_a_table_of_its_schema(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The row of alpha changes."""
        await as_user(seeded, "console_editor", "alpha")

        cursor = await seeded.execute(
            b"UPDATE admin.tables SET cache_ttl = 5 WHERE name = 'p.d.a1'"
        )

        assert cursor.rowcount == 1

    async def test_an_editor_cannot_see_to_update_a_table_of_another_schema(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The row of beta isn't visible, so nothing changes."""
        await as_user(seeded, "console_editor", "alpha")

        cursor = await seeded.execute(
            b"UPDATE admin.tables SET cache_ttl = 5 WHERE name = 'p.d.b1'"
        )

        assert cursor.rowcount == 0

    @pytest.mark.parametrize(
        "statement",
        [
            pytest.param(
                "INSERT INTO admin.tables (name, schema) VALUES ('p.d.b2', 'beta')",
                id="table-in-another-schema",
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name) VALUES ('gamma')",
                id="schema-outside-the-token",
            ),
            pytest.param(
                "INSERT INTO admin.rls (table_name, column_name, unit_type) VALUES ('p.d.b1', 'x', 'u')",
                id="rule-on-another-schema",
            ),
            pytest.param(
                "UPDATE admin.tables SET schema = 'beta' WHERE name = 'p.d.a1'",
                id="move-a-table-to-another-schema",
            ),
        ],
    )
    async def test_an_editor_cannot_write_outside_its_schemas(
        self, seeded: psycopg.AsyncConnection, statement: str
    ) -> None:
        """A row for another schema is refused."""
        await as_user(seeded, "console_editor", "alpha")

        with pytest.raises(InsufficientPrivilege):
            await seeded.execute(statement.encode())

    async def test_an_editor_adds_a_schema_that_its_token_names(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The token can name a schema that doesn't exist yet."""
        await as_user(seeded, "console_editor", "alpha,gamma")

        cursor = await seeded.execute(
            b"INSERT INTO admin.schemas (name) VALUES ('gamma')"
        )

        assert cursor.rowcount == 1

    async def test_the_admin_deletes_in_every_schema(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The admin ignores the schemas of the token."""
        await as_user(seeded, "console_admin", "alpha")

        cursor = await seeded.execute(b"DELETE FROM admin.tables WHERE name = 'p.d.b1'")

        assert cursor.rowcount == 1

    @pytest.mark.parametrize(
        ("role", "allowed"),
        [
            pytest.param("console_viewer", False, id="viewer"),
            pytest.param("console_operator", False, id="operator"),
            pytest.param("console_editor", False, id="editor"),
            pytest.param("console_admin", True, id="admin"),
        ],
    )
    @pytest.mark.parametrize(
        ("relation", "column"),
        [
            pytest.param("admin.queues", "concurrency", id="queue"),
            pytest.param("admin.schedules", "status", id="schedule"),
        ],
    )
    async def test_only_the_admin_changes_queues_and_schedules(
        self,
        admin_db: psycopg.AsyncConnection,
        role: str,
        allowed: bool,
        relation: str,
        column: str,
    ) -> None:
        """Queues and schedules serve every schema, so only the admin edits them."""
        result = await rows(
            admin_db,
            SQL("SELECT has_column_privilege({}, {}, {}, 'UPDATE')")
            .format(Literal(role), Literal(relation), Literal(column))
            .as_string(),
        )

        assert result == [(allowed,)]


@pytest.mark.postgres
class TestScopedStart:
    """An operator starts runs for the schemas of its token only, and the admin starts any run."""

    @staticmethod
    async def scope(connection: psycopg.AsyncConnection, call: str) -> list[str]:
        """Return the schemas that a run with these arguments covers."""
        cursor = await connection.execute(f"SELECT admin.start_scope({call})".encode())
        row = await cursor.fetchone()
        assert row is not None
        return list(row[0])

    @pytest.mark.parametrize(
        ("call", "wanted"),
        [
            pytest.param(
                "ARRAY[]::text[], ARRAY[]::text[]",
                ["alpha"],
                id="every-schema-of-the-token",
            ),
            pytest.param("ARRAY['alpha'], ARRAY[]::text[]", ["alpha"], id="one-schema"),
            pytest.param(
                "ARRAY[]::text[], ARRAY['p.d.a1']",
                ["alpha"],
                id="one-table-of-the-token",
            ),
        ],
    )
    async def test_an_operator_starts_what_its_schemas_allow(
        self, seeded: psycopg.AsyncConnection, call: str, wanted: list[str]
    ) -> None:
        """The run covers the schemas of the token, never all of them."""
        await as_user(seeded, "console_operator", "alpha")

        assert await self.scope(seeded, call) == wanted

    @pytest.mark.parametrize(
        ("schemas", "call"),
        [
            pytest.param(
                "alpha",
                "ARRAY['beta'], ARRAY[]::text[]",
                id="schema-of-another-token",
            ),
            pytest.param(
                "alpha",
                "ARRAY[]::text[], ARRAY['p.d.b1']",
                id="table-of-another-schema",
            ),
            pytest.param("alpha", "ARRAY['alpha'], ARRAY['p.d.b1']", id="mixed"),
            pytest.param(
                "", "ARRAY[]::text[], ARRAY[]::text[]", id="token-without-schemas"
            ),
            pytest.param(
                "alpha", "ARRAY[]::text[], ARRAY['p.d.none']", id="unknown-table"
            ),
        ],
    )
    async def test_an_operator_cannot_start_outside_its_schemas(
        self, seeded: psycopg.AsyncConnection, schemas: str, call: str
    ) -> None:
        """The database refuses with a permission error, and start_sync enqueues nothing."""
        await as_user(seeded, "console_operator", schemas)

        with pytest.raises(InsufficientPrivilege):
            await seeded.execute(f"SELECT admin.start_sync({call})".encode())

    @pytest.mark.parametrize(
        ("call", "wanted"),
        [
            pytest.param("ARRAY['beta'], ARRAY[]::text[]", ["beta"], id="any-schema"),
            pytest.param("ARRAY[]::text[], ARRAY[]::text[]", [], id="every-schema"),
        ],
    )
    async def test_the_admin_starts_any_run(
        self, seeded: psycopg.AsyncConnection, call: str, wanted: list[str]
    ) -> None:
        """The admin ignores the schemas of the token."""
        await as_user(seeded, "console_admin", "alpha")

        assert await self.scope(seeded, call) == wanted


@pytest.mark.postgres
class TestStartNeedsAProvisionedSchema:
    """A run can't start for a schema that the provision job hasn't created yet."""

    async def test_an_unprovisioned_schema_is_a_conflict(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The editor sees a 409 with the reason, instead of a run that fails."""
        await as_user(seeded, "console_operator", "alpha")

        with pytest.raises(psycopg.Error) as error:
            await seeded.execute(
                b"SELECT admin.start_sync(ARRAY['alpha'], ARRAY[]::text[])"
            )

        assert error.value.sqlstate == "PT409"

    async def test_a_provisioned_schema_starts(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The schema and its access policy exist, so the run starts."""
        await seeded.execute(b"CREATE SCHEMA alpha")
        await seeded.execute(b"CREATE TABLE alpha.access_policy (id integer)")
        await as_user(seeded, "console_operator", "alpha")

        cursor = await seeded.execute(
            b"SELECT admin.start_sync(ARRAY['alpha'], ARRAY[]::text[])"
        )

        assert await cursor.fetchone() is not None

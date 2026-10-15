import json
from collections.abc import AsyncIterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
import pytest

from tests.constants import POSTGRES_URL
from tests.fixtures.types import Postgrest
from tests.helpers import sign_token

Response = tuple[int, dict[str, object] | list[dict[str, object]] | str | None]

ALPHA = ("alpha",)


def call(
    service: Postgrest,
    method: str,
    path: str,
    role: str,
    body: object = None,
    schemas: tuple[str, ...] = (),
) -> Response:
    """Send one request to the admin schema as a user with one role and the schemas of its token."""
    token = sign_token(
        {
            "role": role,
            "preferred_username": "ana",
            "sub": "u1",
            "schemas": list(schemas),
        }
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Profile": "admin",
        "Content-Profile": "admin",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    request = Request(  # ruff: ignore[suspicious-url-open-usage]
        service.url + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    payload: bytes
    status: int
    try:
        with urlopen(request, timeout=5) as response:  # ruff: ignore[suspicious-url-open-usage]
            payload = response.read()
            status = response.status
    except HTTPError as error:
        payload = error.read()
        status = error.code
    if not payload:
        return status, None
    try:
        return status, json.loads(payload)
    except json.JSONDecodeError:
        return status, payload.decode()


@pytest.fixture
async def committed() -> AsyncIterator[psycopg.AsyncConnection]:
    """Give a connection for setup and remove what the HTTP requests committed."""
    connection = await psycopg.AsyncConnection.connect(
        f"{POSTGRES_URL}/test", autocommit=True
    )
    try:
        yield connection
    finally:
        await connection.execute(b"DELETE FROM dbos.workflow_status")
        await connection.execute(b"DELETE FROM admin.rls")
        await connection.execute(b"DELETE FROM admin.tables")
        await connection.execute(b"DELETE FROM admin.schemas")
        await connection.execute(b"DELETE FROM dbos.queues WHERE name = 'q-http'")
        await connection.execute(
            b"DELETE FROM dbos.workflow_schedules WHERE schedule_name = 's-http'"
        )
        await connection.close()


@pytest.mark.postgrest
class TestConsoleOverHttp:
    """The JWT role picks the permission level, and errors map to HTTP statuses."""

    @pytest.mark.usefixtures("committed")
    def test_a_viewer_reads_a_view_and_an_anonymous_user_cannot(
        self, postgrest: Postgrest
    ) -> None:
        """The console role can read, and the anonymous role is refused."""
        viewer = call(postgrest, "GET", "/runs", "console_viewer")
        anonymous = call(postgrest, "GET", "/runs", "anon")

        assert (viewer, anonymous[0]) == ((200, []), 401)

    async def test_an_admin_patches_a_queue_and_a_bad_value_is_a_bad_request(
        self, postgrest: Postgrest, committed: psycopg.AsyncConnection
    ) -> None:
        """A PATCH changes the given setting only, and a value that DBOS rejects is 400."""
        await committed.execute(
            b"INSERT INTO dbos.queues (name, concurrency, worker_concurrency) VALUES ('q-http', 4, 2)"
        )

        changed = call(
            postgrest,
            "PATCH",
            "/queues?name=eq.q-http",
            "console_admin",
            {"concurrency": 6},
        )
        refused = call(
            postgrest,
            "PATCH",
            "/queues?name=eq.q-http",
            "console_admin",
            {"concurrency": 0},
        )
        operator = call(
            postgrest,
            "PATCH",
            "/queues?name=eq.q-http",
            "console_operator",
            {"concurrency": 6},
        )

        assert changed[0] == 200
        assert isinstance(changed[1], list)
        assert (changed[1][0]["concurrency"], changed[1][0]["worker_concurrency"]) == (
            6,
            2,
        )
        assert (refused[0], operator[0]) == (400, 403)

    async def test_an_operator_starts_a_run_under_the_name_of_the_token(
        self, postgrest: Postgrest, committed: psycopg.AsyncConnection
    ) -> None:
        """start_sync answers with the run id, and the run records who started it."""
        await committed.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")

        started = call(
            postgrest,
            "POST",
            "/rpc/start_sync",
            "console_operator",
            {},
            schemas=("alpha",),
        )
        runs = call(postgrest, "GET", "/runs", "console_admin")
        viewer = call(postgrest, "POST", "/rpc/start_sync", "console_viewer", {})

        assert (started[0], viewer[0]) == (200, 403)
        assert isinstance(started[1], str)
        assert isinstance(runs[1], list)
        assert [(run["id"], run["started_by"]) for run in runs[1]] == [
            (started[1], "ana")
        ]


@pytest.mark.postgrest
@pytest.mark.usefixtures("committed")
class TestConfigurationOverHttp:
    """Editors build the configuration with plain requests, and consumers read it from one endpoint."""

    def test_an_editor_builds_a_configuration_and_the_endpoint_serves_it(
        self, postgrest: Postgrest
    ) -> None:
        """Schema, table, and rule go in as rows, and sync.json comes out."""
        editor = "console_editor"
        statuses = [
            call(
                postgrest,
                "POST",
                "/schemas",
                editor,
                {"name": "alpha", "claim": "sub"},
                ALPHA,
            )[0],
            call(
                postgrest,
                "POST",
                "/tables",
                editor,
                {
                    "name": "p.d.one",
                    "schema": "alpha",
                    "strategy": "partitioned",
                    "n": 4,
                },
                ALPHA,
            )[0],
            call(
                postgrest,
                "POST",
                "/rls",
                editor,
                {
                    "table_name": "p.d.one",
                    "column_name": "unit_id",
                    "unit_type": "unit",
                },
                ALPHA,
            )[0],
        ]

        document = call(
            postgrest, "GET", "/rpc/sync_config", "console_viewer", None, ALPHA
        )

        assert statuses == [201, 201, 201]
        assert document == (
            200,
            {
                "schemas": {
                    "alpha": {
                        "source": {"type": "bigquery"},
                        "claim": "sub",
                        "tables": [
                            {
                                "name": "p.d.one",
                                "strategy": "partitioned",
                                "n": 4,
                                "rls": [{"column": "unit_id", "unit_type": "unit"}],
                            }
                        ],
                    }
                }
            },
        )

    def test_constraint_failures_map_to_statuses(self, postgrest: Postgrest) -> None:
        """A bad value is 400, and a clash with other rows is 409."""
        editor = "console_editor"
        names = ("alpha", "Bad-Name", "none")
        call(postgrest, "POST", "/schemas", editor, {"name": "alpha"}, names)

        bad_name = call(
            postgrest, "POST", "/schemas", editor, {"name": "Bad-Name"}, names
        )
        duplicate = call(
            postgrest, "POST", "/schemas", editor, {"name": "alpha"}, names
        )
        bad_strategy = call(
            postgrest,
            "POST",
            "/tables",
            editor,
            {"name": "p.d.one", "schema": "alpha", "strategy": "nope"},
            names,
        )
        unknown_schema = call(
            postgrest,
            "POST",
            "/tables",
            editor,
            {"name": "p.d.two", "schema": "none"},
            names,
        )

        assert (bad_name[0], duplicate[0], bad_strategy[0], unknown_schema[0]) == (
            400,
            409,
            400,
            409,
        )

    def test_only_admins_delete(self, postgrest: Postgrest) -> None:
        """An editor gets 403, and an admin removes the row."""
        call(postgrest, "POST", "/schemas", "console_editor", {"name": "alpha"}, ALPHA)

        editor = call(
            postgrest, "DELETE", "/schemas?name=eq.alpha", "console_editor", None, ALPHA
        )
        admin = call(postgrest, "DELETE", "/schemas?name=eq.alpha", "console_admin")
        left = call(postgrest, "GET", "/schemas", "console_viewer", None, ALPHA)

        assert (editor[0], admin[0], left) == (403, 200, (200, []))

    def test_an_admin_imports_a_document(self, postgrest: Postgrest) -> None:
        """The import takes a document and the endpoint returns the same document."""
        document: dict[str, object] = {
            "schemas": {"alpha": {"source": {"type": "bigquery"}, "tables": []}}
        }

        imported = call(
            postgrest,
            "POST",
            "/rpc/import_config",
            "console_admin",
            {"document": document},
        )
        editor = call(
            postgrest,
            "POST",
            "/rpc/import_config",
            "console_editor",
            {"document": document},
            ALPHA,
        )
        served = call(
            postgrest, "GET", "/rpc/sync_config", "console_viewer", None, ALPHA
        )

        assert (imported[0], editor[0], served) == (204, 403, (200, document))

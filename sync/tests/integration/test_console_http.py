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


def call(
    service: Postgrest,
    method: str,
    path: str,
    role: str,
    body: object = None,
) -> Response:
    """Send one request to the admin schema as a user with one role and return status and body."""
    token = sign_token({"role": role, "preferred_username": "ana", "sub": "u1"})
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

    async def test_an_operator_patches_a_queue_and_a_bad_value_is_a_bad_request(
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
            "console_operator",
            {"concurrency": 6},
        )
        refused = call(
            postgrest,
            "PATCH",
            "/queues?name=eq.q-http",
            "console_operator",
            {"concurrency": 0},
        )
        viewer = call(
            postgrest,
            "PATCH",
            "/queues?name=eq.q-http",
            "console_viewer",
            {"concurrency": 6},
        )

        assert changed[0] == 200
        assert isinstance(changed[1], list)
        assert (changed[1][0]["concurrency"], changed[1][0]["worker_concurrency"]) == (
            6,
            2,
        )
        assert (refused[0], viewer[0]) == (400, 403)

    async def test_an_operator_starts_a_run_under_the_name_of_the_token(
        self, postgrest: Postgrest
    ) -> None:
        """start_sync answers with the run id, and the run records who started it."""
        started = call(postgrest, "POST", "/rpc/start_sync", "console_operator", {})
        runs = call(postgrest, "GET", "/runs", "console_viewer")
        viewer = call(postgrest, "POST", "/rpc/start_sync", "console_viewer", {})

        assert (started[0], viewer[0]) == (200, 403)
        assert isinstance(started[1], str)
        assert isinstance(runs[1], list)
        assert [(run["id"], run["started_by"]) for run in runs[1]] == [
            (started[1], "ana")
        ]

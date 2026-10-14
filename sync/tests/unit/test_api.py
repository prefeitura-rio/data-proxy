from httpx2 import ASGITransport, AsyncClient

from api.dependencies import get_client
from api.main import create_app


class FakeClient:
    """A DBOS client that answers from memory."""

    connected: bool

    def __init__(self, *, connected: bool = True) -> None:
        """Create a client that is connected or not."""
        self.connected = connected

    async def check_connection_async(self) -> None:
        """Fail like the DBOS client does when the database is down."""
        if not self.connected:
            raise ConnectionError("database is down")


async def request_to(fake: FakeClient, path: str) -> tuple[int, dict[str, object]]:
    """Send one GET request to an app that uses the fake client."""
    app = create_app()
    app.dependency_overrides[get_client] = lambda: fake
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get(path)
    return response.status_code, response.json()


class TestHealth:
    """Health and readiness behavior."""

    async def test_reports_that_the_process_is_alive(self) -> None:
        """Answer the liveness probe without touching the database."""
        assert await request_to(FakeClient(connected=False), "/health") == (
            200,
            {"status": "ok"},
        )

    async def test_reports_ready_when_the_database_answers(self) -> None:
        """Answer the readiness probe when the DBOS database is reachable."""
        assert await request_to(FakeClient(), "/ready") == (200, {"status": "ok"})

    async def test_reports_not_ready_when_the_database_is_down(self) -> None:
        """Answer 503 with the reason when the DBOS database is unreachable."""
        status, body = await request_to(FakeClient(connected=False), "/ready")

        assert status == 503
        assert body == {"detail": "database is down"}

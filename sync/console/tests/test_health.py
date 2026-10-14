import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from helpers import FakeClient, request_to


class TestHealth:
    """Health and readiness behavior."""

    async def test_reports_that_the_process_is_alive(self) -> None:
        """Answer the liveness probe without touching a database."""
        assert await request_to(FakeClient(connected=False), "/health") == (
            200,
            {"status": "ok"},
        )

    @pytest.mark.postgres
    async def test_reports_ready_when_both_databases_answer(
        self, engine: AsyncEngine
    ) -> None:
        """Answer the readiness probe when DBOS and the app database are reachable."""
        assert await request_to(FakeClient(), "/ready", engine) == (
            200,
            {"status": "ok"},
        )

    async def test_reports_not_ready_when_the_app_database_is_down(self) -> None:
        """Answer 503 with a generic reason, without the details of the failure."""
        status, body = await request_to(FakeClient(), "/ready")

        assert status == 503
        assert body == {"title": "database unavailable", "status": 503, "errors": []}

    @pytest.mark.postgres
    async def test_reports_not_ready_when_dbos_is_down(
        self, engine: AsyncEngine
    ) -> None:
        """Answer 503 when only the DBOS database fails."""
        status, body = await request_to(FakeClient(connected=False), "/ready", engine)

        assert status == 503
        assert body == {"title": "database unavailable", "status": 503, "errors": []}

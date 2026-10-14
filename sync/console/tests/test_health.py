import pytest

from helpers import FakeClient, FakePool, request_to


class TestHealth:
    """Health and readiness behavior."""

    async def test_reports_that_the_process_is_alive(self) -> None:
        """Answer the liveness probe without touching the database."""
        pool = FakePool(reachable=False)

        assert await request_to(FakeClient(connected=False), "/health", pool) == (
            200,
            {"status": "ok"},
        )
        assert pool.queries == []

    async def test_reports_ready_when_both_databases_answer(self) -> None:
        """Answer the readiness probe when DBOS and the app database are reachable."""
        pool = FakePool()

        assert await request_to(FakeClient(), "/ready", pool) == (
            200,
            {"status": "ok"},
        )
        assert pool.queries == ["SELECT 1"]

    async def test_reports_not_ready_when_the_app_database_is_down(self) -> None:
        """Answer 503 with the same generic reason when only the pool fails."""
        status, body = await request_to(
            FakeClient(), "/ready", FakePool(reachable=False)
        )

        assert status == 503
        assert body == {"detail": "database unavailable"}

    async def test_logs_the_reason_that_the_app_database_is_down(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Keep the pool error in the log, where the operator can read it."""
        with caplog.at_level("ERROR"):
            await request_to(FakeClient(), "/ready", FakePool(reachable=False))

        assert "pool is down" in caplog.text

    async def test_reports_not_ready_when_the_database_is_down(self) -> None:
        """Answer 503 with a generic reason, without the details of the failure."""
        status, body = await request_to(FakeClient(connected=False), "/ready")

        assert status == 503
        assert body == {"detail": "database unavailable"}

    async def test_logs_the_reason_that_the_database_is_down(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Keep the real error in the log, where the operator can read it."""
        with caplog.at_level("ERROR"):
            await request_to(FakeClient(connected=False), "/ready")

        assert "database is down" in caplog.text

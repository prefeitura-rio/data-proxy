"""Tests for worker error handling and the process exit code."""

from unittest.mock import AsyncMock

import pytest
from faststream.exceptions import StopApplication

from dp import errors
from dp.models import AllSelection, DumpTask
from dp.state_machines import worker_state as state


@pytest.fixture(autouse=True)
def reset_failure() -> None:
    """Start each test without a recorded failure."""
    state.model.state = "idle"
    state.__init__(model=state.model)


class TestExitCode:
    """Tests for the exit code that a worker Job reports."""

    @pytest.mark.asyncio
    async def test_stop_on_error_records_a_failure_and_stops(self) -> None:
        """
        GIVEN: a subscriber that raised.
        WHEN: stop_on_error handles the error.
        THEN: the process stops and the exit code marks the failure.
        """
        with pytest.raises(StopApplication):
            await errors.stop_on_error(RuntimeError("boom"))

        assert state.exit_code == 1

    def test_exit_code_is_zero_without_a_failure(self) -> None:
        """
        GIVEN: no subscriber failure.
        WHEN: exit_code is read.
        THEN: it returns zero, so a successful Job completes.
        """
        assert state.exit_code == 0

    @pytest.mark.asyncio
    async def test_retried_dump_keeps_the_exit_code_clean(self) -> None:
        """
        GIVEN: a dump task that retries.
        WHEN: retry_or_stop handles the error.
        THEN: the retry does not mark the process as failed.
        """
        task = DumpTask(
            run_id="r1",
            table="p.d.t",
            bucket_path="s3://b/t",
            selections=[AllSelection()],
        )

        with pytest.raises(StopApplication):
            await errors.retry_or_stop(
                RuntimeError("boom"), task, AsyncMock(), max_retries=3
            )

        assert state.exit_code == 0

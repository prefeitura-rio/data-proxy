"""Tests for workflow metric observation."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data_proxy.metrics import RunStatus, observe_sync


class TestObserveSync:
    """ObserveSync behavior tests."""

    @given(status=st.sampled_from(["success", "no_changes"]))
    @pytest.mark.asyncio
    async def test_records_success_and_no_change_statuses(
        self, status: RunStatus
    ) -> None:
        """Record successful and no-change statuses."""
        recorded: list[RunStatus] = []

        async def record(value: RunStatus) -> None:
            recorded.append(value)

        @observe_sync(record)
        async def workflow() -> RunStatus:
            return status

        assert await workflow() is None
        assert recorded == [status]

    @pytest.mark.asyncio
    async def test_records_failure_before_reraising(self) -> None:
        """Record failure before re-raising the exception."""
        recorded: list[RunStatus] = []

        async def record(value: RunStatus) -> None:
            recorded.append(value)

        @observe_sync(record)
        async def workflow() -> RunStatus:
            raise RuntimeError("failed")

        with pytest.raises(RuntimeError, match="failed"):
            await workflow()
        assert recorded == ["failure"]

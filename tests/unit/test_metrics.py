"""Tests for workflow metric observation."""

from typing import Literal

import pytest

from data_proxy.metrics import observe


class TestObserveSync:
    """ObserveSync behavior tests."""

    @pytest.mark.asyncio
    async def test_records_success_for_completed_workflow(self) -> None:
        """Record success when the workflow completes without an exception."""
        recorded: list[str] = []

        async def record(value: Literal["success", "failure"]) -> None:
            recorded.append(value)

        @observe(record)
        async def workflow() -> None:
            return

        assert await workflow() is None
        assert recorded == ["success"]

    @pytest.mark.asyncio
    async def test_preserves_workflow_arguments(self) -> None:
        """Forward positional and keyword arguments to the decorated workflow."""
        recorded: list[str] = []
        calls: list[tuple[int, bool]] = []

        async def record(value: Literal["success", "failure"]) -> None:
            recorded.append(value)

        @observe(record)
        async def workflow(count: int, *, dry_run: bool) -> None:
            calls.append((count, dry_run))

        await workflow(3, dry_run=True)

        assert calls == [(3, True)]
        assert recorded == ["success"]

    @pytest.mark.asyncio
    async def test_records_failure_before_reraising(self) -> None:
        """Record failure before re-raising the exception."""
        recorded: list[str] = []

        async def record(value: Literal["success", "failure"]) -> None:
            recorded.append(value)

        @observe(record)
        async def workflow() -> None:
            raise RuntimeError("failed")

        with pytest.raises(RuntimeError, match="failed"):
            await workflow()
        assert recorded == ["failure"]

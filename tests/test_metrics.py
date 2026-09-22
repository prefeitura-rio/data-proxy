"""Tests for workflow metric observation."""

import pytest

from dp.metrics import RunStatus, observe_sync


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "no_changes"])
async def test_observe_sync_records_terminal_status(status: RunStatus) -> None:
    recorded: list[RunStatus] = []

    async def record(value: RunStatus) -> None:
        recorded.append(value)

    @observe_sync(record)
    async def workflow() -> RunStatus:
        return status

    assert await workflow() is None
    assert recorded == [status]


@pytest.mark.asyncio
async def test_observe_sync_records_failure_and_reraises() -> None:
    recorded: list[RunStatus] = []

    async def record(value: RunStatus) -> None:
        recorded.append(value)

    @observe_sync(record)
    async def workflow() -> RunStatus:
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await workflow()

    assert recorded == ["failure"]

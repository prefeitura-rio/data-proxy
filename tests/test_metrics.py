"""Tests for the metrics tracker decorator."""

from unittest.mock import AsyncMock, patch

import pytest
from faststream.exceptions import StopApplication

from dp.metrics import tracker


@pytest.mark.asyncio
async def test_tracker_pushes_metrics_on_normal_return() -> None:
    """
    GIVEN: a decorated function that returns normally.
    WHEN: it is called.
    THEN: push_to_gateway is called after the function completes.
    """

    @tracker("test-job")
    async def worker() -> None:
        pass

    with patch("dp.metrics.push_to_gateway", new_callable=AsyncMock) as push:
        await worker()

    push.assert_awaited_once()


@pytest.mark.asyncio
async def test_tracker_pushes_metrics_on_stop_application() -> None:
    """
    GIVEN: a decorated function that raises StopApplication.
    WHEN: it is called.
    THEN: push_to_gateway is called in the finally block before the
          exception propagates.
    """

    @tracker("test-job")
    async def worker() -> None:
        raise StopApplication

    with (
        patch("dp.metrics.push_to_gateway", new_callable=AsyncMock) as push,
        pytest.raises(StopApplication),
    ):
        await worker()

    push.assert_awaited_once()


@pytest.mark.asyncio
async def test_tracker_pushes_metrics_on_runtime_error() -> None:
    """
    GIVEN: a decorated function that raises RuntimeError.
    WHEN: it is called.
    THEN: push_to_gateway is called in the finally block before the
          exception propagates.
    """

    @tracker("test-job")
    async def worker() -> None:
        raise RuntimeError("boom")

    with (
        patch("dp.metrics.push_to_gateway", new_callable=AsyncMock) as push,
        pytest.raises(RuntimeError, match="boom"),
    ):
        await worker()

    push.assert_awaited_once()

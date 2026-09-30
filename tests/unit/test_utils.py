"""Unit tests for the readiness wait helper."""

import pytest

from data_proxy.utils import wait_for


class TestWaitFor:
    """Readiness wait behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_when_check_succeeds(self) -> None:
        """Return after the readiness check succeeds."""
        attempts = 0

        async def check() -> None:
            nonlocal attempts
            attempts += 1

        await wait_for(check, timeout=0, interval=0, message="timed out")
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_translates_check_failure_to_timeout(self) -> None:
        """Translate a failed readiness check into a timeout."""
        attempts = 0

        async def check() -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("not ready")

        with pytest.raises(TimeoutError, match="timed out"):
            await wait_for(check, timeout=0, interval=0, message="timed out")
        assert attempts == 1

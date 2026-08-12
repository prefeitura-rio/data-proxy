"""Tests for FastStream Job error handling."""

import pytest
from faststream.exceptions import StopApplication

from dp.sync.errors import stop_on_error


@pytest.mark.asyncio
async def test_stop_on_error_exits_with_failure() -> None:
    error = RuntimeError("failed")

    with pytest.raises(StopApplication) as result:
        await stop_on_error(error)

    assert result.value.code == 1
    assert result.value.__cause__ is error

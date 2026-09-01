"""Tests for FastStream subscriber error handling."""

import pytest
from faststream.exceptions import StopApplication
from loguru import logger

from dp.errors import stop_on_error


class TestErrors:
    """Tests for error handling behavior."""

    @pytest.mark.asyncio
    async def test_stop_on_error_logs_then_raises_stop_application(
        self,
    ) -> None:
        """
        GIVEN: a subscriber raises RuntimeError.
        WHEN: stop_on_error handles the error.
        THEN: the error is logged and StopApplication is raised with the original cause.
        """
        error = RuntimeError("boom")
        messages: list[str] = []
        sink_id = logger.add(messages.append, level="ERROR")

        try:
            with pytest.raises(StopApplication) as excinfo:
                await stop_on_error(error)
        finally:
            logger.remove(sink_id)

        assert excinfo.value.__cause__ is error
        assert len(messages) == 1
        assert "Subscriber failed" in messages[0]
        assert "boom" in messages[0]

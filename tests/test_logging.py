"""Tests for the loguru sink configuration."""

import pytest
from loguru import logger

from dp.logging import configure_logging


def test_configure_logging_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """configure_logging installs a sink that emits log records to stderr."""
    configure_logging()

    logger.error("test message")

    assert "test message" in capsys.readouterr().err

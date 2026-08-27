"""Structured Loguru configuration for pipeline components."""

import sys
from time import monotonic

from loguru import logger


def configure_logging() -> None:
    """Configure one structured stderr sink for Kubernetes logs."""
    logger.remove()
    logger.add(
        sys.stderr,
        serialize=True,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )


def elapsed_ms(started: float) -> int:
    """Return elapsed monotonic time in milliseconds."""
    return int((monotonic() - started) * 1000)

"""Pipeline logger."""

from logging import getLogger
from time import monotonic

logger = getLogger("dp")


def elapsed_ms(started: float) -> int:
    """Return elapsed monotonic time in milliseconds."""
    return int((monotonic() - started) * 1000)

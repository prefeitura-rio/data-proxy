"""FastStream error handling for finite Kubernetes Jobs."""

from typing import NoReturn

from faststream.exceptions import StopApplication
from loguru import logger


class SyncPlanNotFoundError(RuntimeError):
    """A finalization message refers to an expired synchronization plan."""


async def stop_on_error(error: Exception) -> NoReturn:
    """Log a subscriber failure, then stop the process.

    StopApplication is a FastStream IgnoredException: its logging
    middleware skips printing a traceback for it, and FastStream swallows
    it internally instead of exiting with a failure status. Logging the
    original exception here is the only way its cause is ever recorded.
    """
    logger.opt(exception=error).error("Subscriber failed — stopping application")
    raise StopApplication(1) from error

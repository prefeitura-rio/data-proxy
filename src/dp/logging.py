"""Loguru sink configuration for the sync pipeline's Kubernetes Jobs."""

import sys

from loguru import logger


def configure_logging() -> None:
    """Replace loguru's default sink with one safe for production logs.

    ``diagnose=False`` disables printing local variable values inside
    tracebacks, which would otherwise risk leaking secrets from
    ``settings.py`` (GCS/Postgres credentials) into pod logs.
    """
    logger.remove()
    logger.add(sys.stderr, backtrace=True, diagnose=False, colorize=True)

"""JSON logging configuration for pipeline components."""

import json
import logging
import sys
from datetime import UTC, datetime
from time import monotonic
from typing import override

from loguru import logger


class JsonFormatter(logging.Formatter):
    """Format standard-library records as one JSON object per line."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Configure all application logs as one JSON object per line."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,
    )

    logger.remove()
    logger.add(
        sys.stderr,
        serialize=True,
        enqueue=True,
        colorize=False,
        backtrace=False,
        diagnose=False,
    )


def elapsed_ms(started: float) -> int:
    """Return elapsed monotonic time in milliseconds."""
    return int((monotonic() - started) * 1000)

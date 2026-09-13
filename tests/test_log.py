"""Tests for the pipeline log formatter."""

import io
import logging

from dp.log import ContextFilter, ContextFormatter


def _logger_with_stream() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ContextFormatter(datefmt="%Y-%m-%d %H:%M:%S"))

    logger = logging.getLogger("dp-log-test")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.addFilter(ContextFilter())
    logger.setLevel("INFO")
    logger.propagate = False
    return logger, stream


def _boom() -> None:
    """Raise a sample error."""
    raise ValueError("boom")


def test_formatter_appends_the_traceback() -> None:
    """
    GIVEN: a logger that uses the pipeline formatter.
    WHEN: an exception is logged.
    THEN: the output carries the traceback, so a failure is diagnosable.
    """
    logger, stream = _logger_with_stream()

    try:
        _boom()
    except ValueError:
        logger.exception("Subscriber failed")

    output = stream.getvalue()
    assert "Subscriber failed" in output
    assert "Traceback" in output
    assert "ValueError: boom" in output


def test_formatter_keeps_plain_messages_unchanged() -> None:
    """
    GIVEN: a logger that uses the pipeline formatter.
    WHEN: a message without an exception is logged.
    THEN: the output is a single line without a traceback.
    """
    logger, stream = _logger_with_stream()

    logger.info("Seed started plans=%d", 2)

    output = stream.getvalue()
    assert output.endswith("Seed started plans=2\n")
    assert "Traceback" not in output

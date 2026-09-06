"""Pipeline logger with context auto-injection via contextvars."""

import contextvars
from logging import Filter, Formatter, LogRecord, StreamHandler, getLogger
from time import monotonic
from typing import override

runid = contextvars.ContextVar("runid", default="-")
tablename = contextvars.ContextVar("tablename", default="-")
schemaname = contextvars.ContextVar("schemaname", default="-")


class ContextFilter(Filter):
    """Inject run_id, table, and schema into every log record."""

    @override
    def filter(self, record: LogRecord) -> bool:
        record.runid = runid.get()
        record.table = tablename.get()
        record.schema = schemaname.get()
        return True


handler = StreamHandler()
handler.setFormatter(
    Formatter(
        "%(asctime)s %(levelname)s [run_id=%(runid)s table=%(table)s schema=%(schema)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logger = getLogger("dp")
logger.setLevel("INFO")
logger.addFilter(ContextFilter())
logger.addHandler(handler)
logger.propagate = False


def elapsed_ms(started: float) -> int:
    """Return elapsed monotonic time in milliseconds."""
    return int((monotonic() - started) * 1000)

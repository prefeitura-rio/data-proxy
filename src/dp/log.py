"""Pipeline logger with context auto-injection via contextvars."""

from contextvars import ContextVar
from logging import Filter, Formatter, LogRecord, StreamHandler, getLogger
from time import monotonic
from typing import override

runid = ContextVar("runid", default="-")
tablename = ContextVar("tablename", default="-")
schemaname = ContextVar("schemaname", default="-")


class ContextFilter(Filter):
    """Inject run_id, table, and schema into every log record."""

    @override
    def filter(self, record: LogRecord) -> bool:
        record.runid = runid.get()
        record.table = tablename.get()
        record.schema = schemaname.get()
        return True


class ContextFormatter(Formatter):
    """Show [run_id=... table=... schema=...] with only non-default fields."""

    @override
    def format(self, record: LogRecord) -> str:
        runid = getattr(record, "runid", "-")
        table = getattr(record, "table", "-")
        schema = getattr(record, "schema", "-")

        parts: list[str] = []

        if runid != "-":
            parts.append(f"run_id={runid}")
        if table != "-":
            parts.append(f"table={table}")
        if schema != "-":
            parts.append(f"schema={schema}")

        prefix = f"[{' '.join(parts)}] " if parts else ""
        return (
            f"{self.formatTime(record, self.datefmt)} {record.levelname} "
            f"{prefix}{record.getMessage()}"
        )


def elapsed_ms(started: float) -> int:
    """Return elapsed monotonic time in milliseconds."""
    return int((monotonic() - started) * 1000)


handler = StreamHandler()
handler.setFormatter(ContextFormatter(datefmt="%Y-%m-%d %H:%M:%S"))

logger = getLogger("dp")
logger.setLevel("INFO")
logger.addFilter(ContextFilter())
logger.addHandler(handler)
logger.propagate = False

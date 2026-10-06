"""Pipeline logger built on DBOS's dbos_logger with domain context injection."""

from contextvars import ContextVar
from logging import ERROR, Filter, Formatter, LogRecord, StreamHandler, getLogger
from pathlib import Path
from traceback import extract_tb
from typing import override

from .constants import LOCATION_FRAMES, PROJECT_PATH
from .settings import settings

schemaname = ContextVar("schemaname", default="-")
tablename = ContextVar("tablename", default="-")

logger = getLogger("dbos")


def summarize_exception(error: BaseException) -> str:
    """Return the type, the message on one line, and the file:line path to the error."""
    message = " ".join(str(error).split())
    summary = f"{type(error).__name__}: {message}"

    project_frames = [
        frame
        for frame in extract_tb(error.__traceback__)
        if PROJECT_PATH in frame.filename
    ]

    path = [
        f"{Path(frame.filename).name}:{frame.lineno}"
        for frame in project_frames[-LOCATION_FRAMES:]
    ]

    if not path:
        return summary

    return f"{summary} ({' -> '.join(path)})"


class DomainContextFilter(Filter):
    """Inject schema and table context that DBOS doesn't provide."""

    @override
    def filter(self, record: LogRecord) -> bool:
        record.schema = schemaname.get()
        record.table = tablename.get()
        return True


class ContextFormatter(Formatter):
    """Show workflow ID, schema, and table context alongside the message."""

    @override
    def format(self, record: LogRecord) -> str:
        workflow_id = getattr(record, "operationUUID", None)
        schema = getattr(record, "schema", "-")
        table = getattr(record, "table", "-")

        parts: list[str] = []

        if workflow_id:
            parts.append(f"wf={workflow_id}")

        if schema != "-":
            parts.append(f"schema={schema}")

        if table != "-":
            parts.append(f"table={table}")

        prefix = f"[{' '.join(parts)}] " if parts else ""
        message = (
            f"{self.formatTime(record, self.datefmt)} {record.levelname} "
            f"{prefix}{record.getMessage()}"
        )

        if record.exc_info and record.levelno >= ERROR:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        elif record.exc_info and record.exc_info[1] is not None:
            message = f"{message}: {summarize_exception(record.exc_info[1])}"

        return message


handler = StreamHandler()
handler.setFormatter(ContextFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(handler)
logger.addFilter(DomainContextFilter())
logger.propagate = False
logger.setLevel(settings.LOG_LEVEL)

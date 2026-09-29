"""Pipeline logger built on DBOS's dbos_logger with domain context injection."""

from contextvars import ContextVar
from logging import Filter, Formatter, LogRecord, StreamHandler, getLogger
from typing import override

schemaname = ContextVar("schemaname", default="-")
tablename = ContextVar("tablename", default="-")

logger = getLogger("dbos")


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

        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        return message


handler = StreamHandler()
handler.setFormatter(ContextFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(handler)
logger.addFilter(DomainContextFilter())
logger.propagate = False
logger.setLevel("INFO")

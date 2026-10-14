"""Write run log lines to the logger and to a DBOS stream that the console follows."""

from logging import ERROR, INFO, WARNING, LogRecord, getLevelName

from dbos import DBOS, WorkflowSerializationFormat
from whenever import Instant

from engine.log import logger, schemaname, tablename

LOG_STREAM = "logs"

type LogField = str | int | float | bool

RESERVED_FIELDS = frozenset(vars(LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
    "schema",
    "table",
}


def render(message: str, fields: dict[str, LogField]) -> str:
    """Return the readable text of a log line: the message, then the fields."""
    if not fields:
        return message

    pairs = " ".join(f"{name}={value}" for name, value in fields.items())
    return f"{message}: {pairs}"


async def write(level: int, message: str, /, **fields: LogField) -> None:
    """Log one line, and add it to the log stream when a workflow runs.

    The logger gets a record that carries the fields as attributes, which suits
    OpenTelemetry. The stream gets one JSON object with the same information,
    which suits an API.
    """
    clashes = sorted(RESERVED_FIELDS & fields.keys())
    if clashes:
        raise ValueError(f"Reserved log field names: {clashes}")

    schema = schemaname.get()
    table = tablename.get()
    context = {
        name: value
        for name, value in (("schema", schema), ("table", table))
        if value != "-"
    }

    logger.log(
        level, render(message, fields), extra={**fields, **context}, stacklevel=3
    )

    workflow_id = DBOS.workflow_id
    if workflow_id is None:
        return

    await DBOS.write_stream_async(
        LOG_STREAM,
        {
            "time": Instant.now().format_iso(),
            "level": getLevelName(level),
            "message": message,
            "fields": fields,
            "workflow_id": workflow_id,
            "schema": context.get("schema"),
            "table": context.get("table"),
        },
        serialization_type=WorkflowSerializationFormat.PORTABLE,
    )


async def info(message: str, /, **fields: LogField) -> None:
    """Log an informational line."""
    await write(INFO, message, **fields)


async def warning(message: str, /, **fields: LogField) -> None:
    """Log a warning line."""
    await write(WARNING, message, **fields)


async def error(message: str, /, **fields: LogField) -> None:
    """Log an error line."""
    await write(ERROR, message, **fields)

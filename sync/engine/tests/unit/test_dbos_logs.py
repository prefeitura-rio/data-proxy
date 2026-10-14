import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from dbos import WorkflowSerializationFormat

from engine.dbos import logs
from engine.log import schemaname, tablename


@pytest.fixture
def stream(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Run inside a workflow whose stream writes go to a recorder."""
    write = AsyncMock()
    monkeypatch.setattr(
        logs, "DBOS", SimpleNamespace(workflow_id="wf-1", write_stream_async=write)
    )
    return write


@pytest.fixture
def captured_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let pytest capture the records of the pipeline logger."""
    monkeypatch.setattr(logging.getLogger("dbos"), "propagate", True)


def written(stream: AsyncMock) -> dict[str, object]:
    """Return the one value that the helper wrote to the stream."""
    stream.assert_awaited_once()
    call = stream.await_args
    assert call is not None
    return cast("dict[str, object]", call.args[1])


def stream_key(stream: AsyncMock) -> object:
    """Return the key of the stream that the helper wrote to."""
    call = stream.await_args
    assert call is not None
    return call.args[0]


def stream_format(stream: AsyncMock) -> object:
    """Return the serialization format of the last stream write."""
    call = stream.await_args
    assert call is not None
    return call.kwargs["serialization_type"]


@pytest.mark.usefixtures("captured_logger")
class TestLogs:
    """Run log behavior tests."""

    @pytest.mark.parametrize(
        ("write", "level"),
        [
            pytest.param(logs.info, "INFO", id="info"),
            pytest.param(logs.warning, "WARNING", id="warning"),
            pytest.param(logs.error, "ERROR", id="error"),
        ],
    )
    async def test_sends_the_same_information_to_the_stream_and_the_logger(
        self,
        stream: AsyncMock,
        caplog: pytest.LogCaptureFixture,
        write: Callable[..., Awaitable[None]],
        level: str,
    ) -> None:
        """Give the stream a structured line and the logger a record with the same fields."""
        with caplog.at_level(logging.DEBUG, logger="dbos"):
            await write("Dump completed", task_id="t-1", failed_paths=2)

        line = written(stream)
        assert stream_key(stream) == "logs"
        assert (line["level"], line["message"]) == (level, "Dump completed")
        assert line["fields"] == {"task_id": "t-1", "failed_paths": 2}
        assert line["workflow_id"] == "wf-1"

        record = caplog.records[0]
        assert record.levelname == level
        attributes = vars(record)
        assert (attributes["task_id"], attributes["failed_paths"]) == ("t-1", 2)

    async def test_writes_a_readable_text_line_to_the_logger(
        self, stream: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Keep the text of a log line readable, with the fields after the message."""
        with caplog.at_level(logging.INFO, logger="dbos"):
            await logs.info("Dump completed", task_id="t-1", failed_paths=2)

        assert caplog.messages == ["Dump completed: task_id=t-1 failed_paths=2"]

    async def test_writes_only_the_message_when_there_are_no_fields(
        self, stream: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Add no colon and no fields to a line that has none."""
        with caplog.at_level(logging.INFO, logger="dbos"):
            await logs.info("100% done")

        assert caplog.messages == ["100% done"]
        assert written(stream)["fields"] == {}

    async def test_writes_the_stream_as_portable_json(self, stream: AsyncMock) -> None:
        """Use the portable format, so the console can read the line."""
        await logs.info("Started")

        assert stream_format(stream) == WorkflowSerializationFormat.PORTABLE

    async def test_stamps_the_line_with_a_utc_time(self, stream: AsyncMock) -> None:
        """Write an ISO 8601 time in UTC."""
        await logs.info("Started")

        assert str(written(stream)["time"]).endswith("Z")

    async def test_adds_the_schema_and_the_table_to_both_outputs(
        self, stream: AsyncMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Copy the schema and the table that the pipeline logger tracks."""
        schema_token = schemaname.set("test")
        table_token = tablename.set("full_table")
        try:
            with caplog.at_level(logging.INFO, logger="dbos"):
                await logs.info("Started")
        finally:
            schemaname.reset(schema_token)
            tablename.reset(table_token)

        line = written(stream)
        assert (line["schema"], line["table"]) == ("test", "full_table")
        attributes = vars(caplog.records[0])
        assert (attributes["schema"], attributes["table"]) == ("test", "full_table")

    async def test_leaves_out_a_schema_and_a_table_that_are_not_set(
        self, stream: AsyncMock
    ) -> None:
        """Write no schema and no table outside any context."""
        await logs.info("Started")

        line = written(stream)
        assert (line["schema"], line["table"]) == (None, None)

    @pytest.mark.parametrize("name", ["message", "args", "name", "schema", "table"])
    async def test_rejects_a_field_that_clashes_with_a_log_record_attribute(
        self, stream: AsyncMock, name: str
    ) -> None:
        """Refuse a field name that would overwrite part of the log record."""
        with pytest.raises(ValueError, match=name):
            await logs.info("Started", **{name: "x"})

        stream.assert_not_awaited()

    async def test_only_logs_outside_a_workflow(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Skip the stream when no workflow runs, as in unit tests of a step."""
        write = AsyncMock()
        monkeypatch.setattr(
            logs, "DBOS", SimpleNamespace(workflow_id=None, write_stream_async=write)
        )

        with caplog.at_level(logging.INFO, logger="dbos"):
            await logs.info("Started", task_id="t-1")

        write.assert_not_awaited()
        assert caplog.messages == ["Started: task_id=t-1"]

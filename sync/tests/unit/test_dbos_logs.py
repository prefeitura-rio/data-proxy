from collections.abc import Awaitable, Callable
from logging import INFO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sync.dbos import logs
from sync.log import SCHEMA, TABLE


def workflow(monkeypatch: pytest.MonkeyPatch, workflow_id: str | None) -> AsyncMock:
    """Run the log functions in a workflow, or outside one, and return the stream writer."""
    write_stream = AsyncMock()
    monkeypatch.setattr(
        logs,
        "DBOS",
        SimpleNamespace(workflow_id=workflow_id, write_stream_async=write_stream),
    )
    return write_stream


def written(stream: AsyncMock) -> tuple[str, dict[str, object]]:
    """Return the key and the object of the one write to the stream."""
    call = stream.await_args
    assert call is not None
    key, entry = call.args

    return key, entry


class TestRender:
    """render behavior tests."""

    @pytest.mark.parametrize(
        ("message", "fields", "expected"),
        [
            pytest.param("Done", {}, "Done", id="no-fields"),
            pytest.param("Done", {"rows": 5}, "Done: rows=5", id="one-field"),
            pytest.param(
                "Done",
                {"rows": 5, "path": "s3://b/t", "ok": True},
                "Done: rows=5 path=s3://b/t ok=True",
                id="fields-in-order",
            ),
        ],
    )
    def test_writes_the_message_and_then_the_fields(
        self, message: str, fields: dict[str, logs.LogField], expected: str
    ) -> None:
        """Make the text that the logger and the console show."""
        assert logs.render(message, fields) == expected


class TestWrite:
    """write behavior tests."""

    @pytest.mark.asyncio
    async def test_adds_one_object_to_the_stream_of_the_workflow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Give the console the level, the message, the fields and the context."""
        stream = workflow(monkeypatch, "wf-1")
        schema_token = SCHEMA.set("app")
        table_token = TABLE.set("p.d.t")

        try:
            await logs.warning("Slow", seconds=3)
        finally:
            SCHEMA.reset(schema_token)
            TABLE.reset(table_token)

        stream.assert_awaited_once()
        key, entry = written(stream)
        assert key == logs.LOG_STREAM
        assert {name: entry[name] for name in entry if name != "time"} == {
            "level": "WARNING",
            "message": "Slow",
            "fields": {"seconds": 3},
            "workflow_id": "wf-1",
            "schema": "app",
            "table": "p.d.t",
        }
        assert isinstance(entry["time"], str)

    @pytest.mark.asyncio
    async def test_leaves_out_the_context_that_is_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Write null for a schema and a table that the workflow has not set."""
        stream = workflow(monkeypatch, "wf-1")

        await logs.info("Started")

        _, entry = written(stream)
        assert (entry["schema"], entry["table"]) == (None, None)

    @pytest.mark.asyncio
    async def test_writes_nothing_to_the_stream_outside_a_workflow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only log, as the code does in a script."""
        stream = workflow(monkeypatch, None)

        await logs.error("No workflow")

        stream.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name", ["message", "schema", "table", "name", "levelname"]
    )
    async def test_refuses_a_field_name_that_the_log_record_uses(
        self, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        """Stop a field from overwriting an attribute of the record."""
        stream = workflow(monkeypatch, "wf-1")

        with pytest.raises(ValueError, match="Reserved log field names"):
            await logs.write(INFO, "Clash", **{name: "x"})

        stream.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("function", "level"),
        [(logs.info, "INFO"), (logs.warning, "WARNING"), (logs.error, "ERROR")],
    )
    async def test_each_helper_writes_its_own_level(
        self,
        monkeypatch: pytest.MonkeyPatch,
        function: Callable[..., Awaitable[None]],
        level: str,
    ) -> None:
        """Map info, warning and error to the names of the levels."""
        stream = workflow(monkeypatch, "wf-1")

        await function("Hello")

        assert written(stream)[1]["level"] == level

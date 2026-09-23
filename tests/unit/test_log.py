"""Tests for structured pipeline log formatting."""

import logging

from data_proxy.log import ContextFormatter, DomainContextFilter, schemaname, tablename


class TestDomainContextFilter:
    """DomainContextFilter behavior tests."""

    def test_injects_context_values(self) -> None:
        """Add schema and table context to a log record."""
        record = logging.LogRecord("dbos", logging.INFO, "", 0, "message", (), None)
        with schemaname.set("app"), tablename.set("people"):
            assert DomainContextFilter().filter(record)
        assert record.__dict__["schema"] == "app"
        assert record.__dict__["table"] == "people"


class TestContextFormatter:
    """ContextFormatter behavior tests."""

    def test_formats_workflow_schema_and_table_context(self) -> None:
        """Include workflow, schema, and table context in the message."""
        record = logging.LogRecord("dbos", logging.INFO, "", 0, "ready", (), None)
        record.operationUUID = "wf-1"
        record.schema = "app"
        record.table = "people"
        rendered = ContextFormatter("%(message)s").format(record)
        assert "wf=wf-1" in rendered
        assert "schema=app" in rendered
        assert "table=people" in rendered
        assert rendered.endswith("ready")

    def test_formats_message_without_context(self) -> None:
        """Omit the context prefix when no context exists."""
        record = logging.LogRecord("dbos", logging.INFO, "", 0, "ready", (), None)
        rendered = ContextFormatter("%(message)s").format(record)
        assert rendered.endswith("INFO ready")

"""Unit tests for persisted synchronization state."""

from data_proxy.models import (
    FullTable,
    PublicationResult,
    SchemaConfig,
    SyncConfig,
    SyncPlan,
)
from data_proxy.settings import settings
from data_proxy.state import build_table_states, schema


class TestTableStateBuilder:
    """TableStateBuilder behavior tests."""

    def test_builds_state_for_published_tables(self, full_table: FullTable) -> None:
        """Build state for published tables."""
        table = FullTable(name="p.app.t", resolved_schema="app")
        config = SyncConfig(schemas={"app": SchemaConfig(tables=[table])})
        plan = SyncPlan(
            schema_name="app",
            signatures={"p.app.t": "sig"},
            paths={"p.app.t": ["s3://b/t"]},
        )
        result = PublicationResult(plan=plan, published_tables={"p.app.t"})
        states = build_table_states(result, config)
        assert set(states) == {"p.app.t"}
        assert states["p.app.t"].signature == "sig"


class TestStateSchema:
    """Application state schema behavior tests."""

    def test_returns_configured_schema_identifier(self) -> None:
        """Return the configured application schema as an identifier."""
        assert schema().as_string(None) == f'"{settings.DBOS_APP_SCHEMA}"'

"""Unit tests for persisted synchronization state."""

from unittest.mock import AsyncMock

import pytest

import data_proxy.state as state
from data_proxy.models import (
    FullTable,
    PartitionManifest,
    PublicationResult,
    SchemaConfig,
    Strategy,
    SyncConfig,
    SyncPlan,
    TableState,
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


class TestReadTableState:
    """ReadTableState behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_none_when_state_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return none for a missing table."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = None
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        assert await state.read_table_state(AsyncMock(), "p.app.people") is None

    @pytest.mark.asyncio
    async def test_decodes_table_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Decode a stored table state."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = (
            '{"strategy":"full","signature":"s","partitions":null}',
        )
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        result = await state.read_table_state(AsyncMock(), "p.app.people")
        assert result == TableState(strategy=Strategy.FULL, signature="s")

    @pytest.mark.asyncio
    async def test_rejects_invalid_stored_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject invalid stored state JSON."""
        cursor = AsyncMock()
        cursor.fetchone.return_value = ("invalid",)
        monkeypatch.setattr(state, "execute_sql", AsyncMock(return_value=cursor))
        with pytest.raises(ValueError, match="json"):
            await state.read_table_state(AsyncMock(), "p.app.people")


class TestReadTableSignature:
    """ReadTableSignature behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_signature_for_existing_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return the signature from existing table state."""
        monkeypatch.setattr(
            state,
            "read_table_state",
            AsyncMock(return_value=TableState(strategy=Strategy.FULL, signature="s")),
        )
        assert await state.read_table_signature(AsyncMock(), "p.app.people") == "s"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return none when table state is absent."""
        monkeypatch.setattr(state, "read_table_state", AsyncMock(return_value=None))
        assert await state.read_table_signature(AsyncMock(), "p.app.people") is None


class TestReadPartitionManifest:
    """ReadPartitionManifest behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_none_without_partitions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return none for full-table state."""
        monkeypatch.setattr(
            state,
            "read_table_state",
            AsyncMock(return_value=TableState(strategy=Strategy.FULL, signature="s")),
        )
        assert await state.read_partition_manifest(AsyncMock(), "p.app.people") is None

    @pytest.mark.asyncio
    async def test_builds_manifest_from_partitioned_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Build a partition manifest from stored state."""
        monkeypatch.setattr(
            state,
            "read_table_state",
            AsyncMock(
                return_value=TableState(
                    strategy=Strategy.PARTITIONED, signature="s", partitions={}
                )
            ),
        )
        result = await state.read_partition_manifest(AsyncMock(), "p.app.people")
        assert result == PartitionManifest(table_signature="s", partitions={})


class TestEmitError:
    """EmitError behavior tests."""

    @pytest.mark.asyncio
    async def test_serializes_error_fields_and_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persist structured fields and commit the error."""
        execute = AsyncMock()
        monkeypatch.setattr(state, "execute_sql", execute)
        connection = AsyncMock()
        await state.emit_error(connection, "failed", table="people", task="task-1")
        execute.assert_awaited_once()
        assert execute.await_args is not None
        assert execute.await_args.args[1] == "postgres/insert_error"
        assert execute.await_args.kwargs["params"]["reason"] == "failed"
        assert '"table": "people"' in execute.await_args.kwargs["params"]["fields"]
        connection.commit.assert_awaited_once()

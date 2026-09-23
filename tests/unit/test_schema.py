"""Tests for PostgreSQL schema initialization orchestration."""

from unittest.mock import AsyncMock

import pytest

import data_proxy.schema as schema
from data_proxy.models import SchemaConfig, SyncConfig


class TestInitializeSchemas:
    """InitializeSchemas behavior tests."""

    @pytest.mark.asyncio
    async def test_installs_shared_objects_before_each_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install roles and procedures before schema objects."""
        calls: list[str] = []

        async def execute(*args: object, **kwargs: object) -> None:
            calls.append(str(args[1]))

        monkeypatch.setattr(schema, "execute_sql", execute)
        monkeypatch.setattr(schema, "ensure_schema_policy_writer", AsyncMock())
        connection = AsyncMock()
        config = SyncConfig(schemas={"app": SchemaConfig()})
        await schema.initialize_schemas(connection, config)
        assert calls[:4] == [
            "postgres/init_roles",
            "postgres/cleanup_stale_objects",
            "postgres/apply_retention",
            "postgres/prune_access_log",
        ]
        assert calls[4:] == ["postgres/init_schema", "postgres/init_access_policy"]
        connection.commit.assert_awaited_once()


class TestRevokeAnonymousAccess:
    """RevokeAnonymousAccess behavior tests."""

    @pytest.mark.asyncio
    async def test_revokes_access_for_every_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Revoke anonymous access from each configured schema."""
        execute = AsyncMock()
        monkeypatch.setattr(schema, "execute_sql", execute)
        connection = AsyncMock()
        config = SyncConfig(schemas={"app": SchemaConfig(), "other": SchemaConfig()})
        await schema.revoke_anonymous_access(connection, config)
        assert execute.await_count == 2
        connection.commit.assert_awaited_once()

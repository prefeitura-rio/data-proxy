"""Tests for PostgreSQL schema initialization orchestration."""

from collections.abc import Mapping
from typing import cast
from unittest.mock import AsyncMock

import pytest
from psycopg.sql import Composable

import data_proxy.schema as schema
from data_proxy.models import SchemaConfig, SyncConfig
from data_proxy.types import TemplateValue


class TestInitializeSchemas:
    """InitializeSchemas behavior tests."""

    @pytest.mark.asyncio
    async def test_installs_shared_objects_before_each_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install roles and procedures before schema objects."""
        calls: list[tuple[str, object]] = []

        async def execute(*args: object, **kwargs: object) -> None:
            calls.append((str(args[1]), kwargs.get("mapping")))

        monkeypatch.setattr(schema, "execute_sql", execute)
        monkeypatch.setattr(schema, "ensure_schema_policy_writer", AsyncMock())
        connection = AsyncMock()
        config = SyncConfig(schemas={"app": SchemaConfig()})
        await schema.initialize_schemas(connection, config)
        assert [path for path, _ in calls[:2]] == [
            "postgres/cleanup_stale_objects",
            "postgres/prune_access_log",
        ]
        assert [path for path, _ in calls[2:]] == [
            "postgres/init_schema",
            "postgres/init_access_policy",
        ]
        first_mapping = cast(Mapping[str, TemplateValue], calls[2][1])
        second_mapping = cast(Mapping[str, TemplateValue], calls[3][1])
        assert cast(Composable, first_mapping["schema"]).as_string(None) == '"app"'
        assert cast(Composable, second_mapping["schema"]).as_string(None) == '"app"'
        connection.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_commit_after_sql_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Do not commit when initialization fails."""
        execute = AsyncMock(side_effect=RuntimeError("failed"))
        monkeypatch.setattr(schema, "execute_sql", execute)
        connection = AsyncMock()
        with pytest.raises(RuntimeError, match="failed"):
            await schema.initialize_schemas(
                connection, SyncConfig(schemas={"app": SchemaConfig()})
            )
        connection.commit.assert_not_awaited()


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

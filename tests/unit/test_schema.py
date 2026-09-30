"""Tests for PostgreSQL schema initialization orchestration."""

from collections.abc import Mapping
from unittest.mock import AsyncMock

import pytest

import data_proxy.executor as executor
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

        def template_name(path: str, mapping: Mapping[str, TemplateValue]) -> str:
            return path

        monkeypatch.setattr(executor, "render_template", template_name)
        monkeypatch.setattr(schema, "ensure_schema_policy_writer", AsyncMock())
        conn = AsyncMock()

        await schema.initialize_schemas(
            conn, SyncConfig(schemas={"app": SchemaConfig()})
        )

        assert [call.args[0] for call in conn.execute.await_args_list] == [
            "postgres/cleanup_stale_objects",
            "postgres/prune_access_log",
            "postgres/init_schema",
            "postgres/init_access_policy",
        ]
        conn.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_commit_after_sql_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Do not commit when initialization fails."""
        conn = AsyncMock()
        conn.execute.side_effect = RuntimeError("failed")
        with pytest.raises(RuntimeError, match="failed"):
            await schema.initialize_schemas(
                conn, SyncConfig(schemas={"app": SchemaConfig()})
            )
        conn.commit.assert_not_awaited()


class TestRevokeAnonymousAccess:
    """RevokeAnonymousAccess behavior tests."""

    @pytest.mark.asyncio
    async def test_revokes_access_for_every_schema(self) -> None:
        """Revoke anonymous access from each configured schema."""
        conn = AsyncMock()
        config = SyncConfig(schemas={"app": SchemaConfig(), "other": SchemaConfig()})
        await schema.revoke_anonymous_access(conn, config)
        assert conn.execute.await_count == 2
        conn.commit.assert_awaited_once()

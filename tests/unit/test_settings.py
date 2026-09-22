"""Unit tests for synchronization settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from data_proxy.models import FullTable
from data_proxy.settings import settings


class TestSyncConfigSettings:
    """Synchronization settings behavior tests."""

    def test_loads_sync_config_from_configured_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Load synchronization configuration from the configured file."""
        path = tmp_path / "sync.json"
        path.write_text(
            '{"schemas": {"app": {"tables": [{"name": "p.d.people", "strategy": "full"}]}}}'
        )
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", path)
        config = settings.sync_config
        assert config.schemas["app"].tables == [
            FullTable(name="p.d.people", resolved_schema="app")
        ]

    def test_rejects_invalid_sync_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject invalid synchronization configuration content."""
        path = tmp_path / "sync.json"
        path.write_text('{"schemas": {"app": {"tables": "invalid"}}}')
        monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", path)
        with pytest.raises(ValidationError):
            assert settings.sync_config.tables == []

    def test_keeps_application_settings_independent_from_sync_config(
        self,
    ) -> None:
        """Keep application settings available without a sync file read."""
        assert settings.DBOS_APP_SCHEMA == "data_proxy"
        assert settings.SCHEMA_WRITERS is not None

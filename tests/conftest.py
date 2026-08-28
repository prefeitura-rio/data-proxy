"""Shared fixtures for the data-proxy test suite."""

from pathlib import Path

import pytest

from dp.settings import settings


@pytest.fixture
def sync_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings at an empty, temporary sync configuration file."""
    config = tmp_path / "sync.json"
    config.write_text('{"schemas": {}}')
    writers = tmp_path / "writers.json"
    writers.write_text('{"writers": {}}')
    monkeypatch.setattr(settings, "SYNC_CONFIG_PATH", config)
    monkeypatch.setattr(settings, "SCHEMA_WRITERS_FILE", writers)
    return config

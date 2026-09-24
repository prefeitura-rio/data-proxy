"""Tests for local DuckLake catalog paths and Litestream configuration."""

from pathlib import Path

import pytest

from data_proxy.catalog import CatalogPaths, litestream_config
from data_proxy.constants import publish_queue
from data_proxy.settings import settings


def test_catalog_paths_use_local_writer_file_and_litestream_replica(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Build stable local and S3 paths for one schema."""
    monkeypatch.setattr(settings, "DUCKLAKE_CATALOG_LOCAL_PATH", tmp_path)
    monkeypatch.setattr(settings, "DUCKLAKE_CATALOG_PATH", "ducklake")
    monkeypatch.setattr(settings, "S3_BUCKET", "data")

    paths = CatalogPaths.for_schema("app")

    assert paths.local == tmp_path / "app" / "catalog.sqlite"
    assert paths.replica == "s3://data/ducklake/app/catalog.sqlite"


def test_litestream_config_contains_all_schema_catalogs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Render one Litestream database entry per schema."""
    monkeypatch.setattr(settings, "DUCKLAKE_CATALOG_LOCAL_PATH", tmp_path)
    monkeypatch.setattr(settings, "DUCKLAKE_CATALOG_PATH", "ducklake")
    monkeypatch.setattr(settings, "S3_BUCKET", "bucket")
    monkeypatch.setattr(settings, "S3_ENDPOINT", "seaweedfs-s3:8333")
    monkeypatch.setattr(settings, "S3_USE_SSL", False)

    rendered = litestream_config(["app", "analytics"])

    assert f"path: {tmp_path / 'app' / 'catalog.sqlite'}" in rendered
    assert "path: ducklake/app/catalog.sqlite" in rendered
    assert f"path: {tmp_path / 'analytics' / 'catalog.sqlite'}" in rendered
    assert "path: ducklake/analytics/catalog.sqlite" in rendered
    assert "endpoint: http://seaweedfs-s3:8333" in rendered
    assert "access-key-id: ${S3_ACCESS_KEY}" in rendered
    assert "secret-access-key: ${S3_SECRET_KEY}" in rendered


def test_publish_queue_is_unique_per_schema() -> None:
    """Use one DBOS queue for each schema publisher."""
    assert publish_queue("app") == "publish:app"
    assert publish_queue("analytics") != publish_queue("app")


def test_litestream_config_has_no_database_entries_for_empty_schema_list() -> None:
    """Render a valid empty configuration when no schemas are configured."""
    assert litestream_config([]) == "dbs: []\n"

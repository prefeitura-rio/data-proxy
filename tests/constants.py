"""Shared constants for the data-proxy test suite."""

from datetime import UTC, datetime
from pathlib import Path

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)
FILES = Path(__file__).parent / "files"
HELM_SQL = Path(__file__).parent.parent / "helm" / "files" / "templates" / "postgres"
TEST_SQL_DIR = Path(__file__).parent / "templates"
PARQUET = str(FILES / "people_partition_10.parquet")
PARQUET_20 = str(FILES / "people_partition_20.parquet")
ROUTED_TABLE = "p.d.routed"

"""Shared constants for the data-proxy test suite."""

from datetime import UTC, datetime
from pathlib import Path

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)
FILES = Path(__file__).parent / "files"

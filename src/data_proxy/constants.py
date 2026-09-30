"""Constants for the synchronization service."""

from pathlib import Path
from types import MappingProxyType

SQL_DIR = Path(__file__).parent / "templates"
DUCKDB_VIEW_PREFIX = "source_"
PROTECTED_VIEW_NAMES = frozenset({"access_policy", "access_log", "state"})

BIGQUERY_TABLE_REFERENCE_PATTERN = (
    r"^(?P<project>[A-Za-z0-9_-]+)"
    r"\.(?P<dataset>[A-Za-z0-9_]+)"
    r"\.(?P<table>[A-Za-z0-9_$-]+)$"
)

TIME_GRANULARITY_SPECS = MappingProxyType(
    {
        "HOUR": ("%Y%m%d%H", "hours", "YYYY-MM-DD hh:mm:ss"),
        "DAY": ("%Y%m%d", "days", "YYYY-MM-DD"),
        "MONTH": ("%Y%m", "months", "YYYY-MM-DD"),
        "YEAR": ("%Y", "years", "YYYY-MM-DD"),
    }
)

DUMP_QUEUE = "dump"
SYNC_QUEUE = "sync"


def publish_queue(schema: str) -> str:
    """Return the DBOS publish queue owned by one schema."""
    return f"publish:{schema}"

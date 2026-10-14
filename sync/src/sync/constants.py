"""Constants for the synchronization service."""

from pathlib import Path
from types import MappingProxyType
from typing import Final

SQL_DIR = Path(__file__).parent / "templates"
DUCKDB_VIEW_PREFIX = "source_"
PROTECTED_VIEW_NAMES = frozenset({"access_policy", "access_log", "state"})
DEFAULT_SOURCE: Final = "bigquery"

BIGQUERY_TABLE_REFERENCE_PATTERN = (
    r"^(?P<project>[A-Za-z0-9_-]+)"
    r"\.(?P<dataset>[A-Za-z0-9_]+)"
    r"\.(?P<table>[A-Za-z0-9_$-]+)$"
)

TIME_GRANULARITY_SPECS = MappingProxyType(
    {
        "HOUR": ("YYYYMMDDhh", "", "hours", "YYYY-MM-DD hh:mm:ss"),
        "DAY": ("YYYYMMDD", "", "days", "YYYY-MM-DD"),
        "MONTH": ("YYYYMMDD", "01", "months", "YYYY-MM-DD"),
        "YEAR": ("YYYYMMDD", "0101", "years", "YYYY-MM-DD"),
    }
)

DUMP_QUEUE = "dump"
SYNC_QUEUE = "sync"
PROXY_QUEUE = "proxy"

RESTARTED_AT_ANNOTATION: Final = "kubectl.kubernetes.io/restartedAt"
PROJECT_PATH: Final = "/data_proxy/"
LOCATION_FRAMES: Final = 3

POSTGREST_SELECTOR: Final = {"data-proxy.io/serving": "postgrest"}


PUBLISH_QUEUE: Final = "publish"

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
        "HOUR": ("%Y%m%d%H", "hours", "YYYY-MM-DD hh:mm:ss"),
        "DAY": ("%Y%m%d", "days", "YYYY-MM-DD"),
        "MONTH": ("%Y%m", "months", "YYYY-MM-DD"),
        "YEAR": ("%Y", "years", "YYYY-MM-DD"),
    }
)

DUMP_QUEUE = "dump"
REFRESH_QUEUE = "refresh"
SYNC_QUEUE = "sync"

CATALOG_VOLUME: Final = "ducklake-catalogs"
NOT_FOUND: Final = 404
RESTARTED_AT_ANNOTATION: Final = "kubectl.kubernetes.io/restartedAt"
COMPONENT_LABEL: Final = "app.kubernetes.io/component"
SCHEMA_LABEL: Final = "data-proxy.io/schema"
TEMPLATE_LABEL: Final = "data-proxy.io/template"
INSTANCE_LABEL: Final = "data-proxy.io/instance"
REFRESH_COMPONENT: Final = "refresh-catalog"
REFRESH_JOB_TTL_SECONDS: Final = 300
JOB_NAME_LIMIT: Final = 63
JOB_SUFFIX_BYTES: Final = 3
PROJECT_PATH: Final = "/data_proxy/"
LOCATION_FRAMES: Final = 3

POSTGRES_SELECTOR: Final = {"cnpg.io/podRole": "instance"}
POOLER_SELECTOR: Final = {"cnpg.io/podRole": "pooler"}
POSTGREST_SELECTOR: Final = {"data-proxy.io/serving": "postgrest"}


def publish_queue(schema: str) -> str:
    """Return the DBOS publish queue owned by one schema."""
    return f"publish:{schema}"

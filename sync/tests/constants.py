from datetime import UTC, datetime
from os import environ
from pathlib import Path

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)
FILES = Path(__file__).parent / "files"
HELM_SQL = Path(__file__).parents[2] / "helm" / "files" / "templates" / "postgres"
RBAC_RULES = Path(__file__).parents[2] / "helm" / "files" / "config" / "rbac.toml"
SOURCE_SQL = Path(__file__).parent.parent / "src" / "sync" / "templates" / "postgres"
TEST_SQL_DIR = Path(__file__).parent / "templates"
PROXY_FILTER_CASES = (
    Path(__file__).parents[2] / "proxy" / "tests" / "unit" / "fixtures" / "filters.json"
)
PARQUET = str(FILES / "people_partition_10.parquet")
ROUTED_TABLE = "p.d.routed"
POSTGRES_HOST = "127.0.0.1"
POSTGRES_PORT = environ.get("TEST_POSTGRES_PORT", "15432")
POSTGRES_URL = f"postgresql://test:test@{POSTGRES_HOST}:{POSTGRES_PORT}"
SILO_ENDPOINT = environ.get("TEST_SILO_ENDPOINT", "127.0.0.1:19000")

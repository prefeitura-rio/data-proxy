from datetime import UTC, datetime
from os import environ
from pathlib import Path

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)
FILES = Path(__file__).parent / "files"
HELM_SQL = Path(__file__).parents[3] / "helm" / "files" / "templates" / "postgres"
SOURCE_SQL = Path(__file__).parent.parent / "src" / "engine" / "templates" / "postgres"
TEST_SQL_DIR = Path(__file__).parent / "templates"
PROXY_FILTER_CASES = (
    Path(__file__).parents[3] / "proxy" / "tests" / "unit" / "fixtures" / "filters.json"
)
PARQUET = str(FILES / "people_partition_10.parquet")
PARQUET_20 = str(FILES / "people_partition_20.parquet")
ROUTED_TABLE = "p.d.routed"
POSTGREST_AUTH = environ.get(
    "TEST_POSTGREST_AUTH", "postgrest-test-secret-012345678901234567890123"
)
CONTAINER_RUNTIME = environ.get("CONTAINER_RUNTIME", "podman")
POSTGRES_CONTAINER = environ.get("TEST_POSTGRES_CONTAINER", "data-proxy-test-postgres")
POSTGRES_HOST = "127.0.0.1"
POSTGRES_PORT = environ.get("TEST_POSTGRES_PORT", "15432")
POSTGRES_URL = f"postgresql://test:test@{POSTGRES_HOST}:{POSTGRES_PORT}"
SILO_ENDPOINT = environ.get("TEST_SILO_ENDPOINT", "127.0.0.1:19000")
VALKEY_PORT = int(environ.get("TEST_VALKEY_PORT", "16379"))
POSTGREST_URL = environ.get("TEST_POSTGREST_URL", "http://127.0.0.1:13000")

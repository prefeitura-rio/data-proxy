from datetime import UTC, datetime
from os import environ
from pathlib import Path
from typing import Final

from sync.models import Strategy, TableState

MODIFIED = datetime(2026, 8, 7, 12, 47, 52, 683000, tzinfo=UTC)
FILES = Path(__file__).parent / "files"
HELM_SQL = Path(__file__).parents[2] / "helm" / "files" / "templates" / "postgres"
RBAC_RULES = Path(__file__).parents[2] / "helm" / "files" / "config" / "rbac.toml"
TEST_SQL_DIR = Path(__file__).parent / "templates"
PROXY_FILTER_CASES = (
    Path(__file__).parents[2] / "proxy" / "tests" / "unit" / "fixtures" / "filters.json"
)
PROXY_CONSOLE_CONFIG = (
    Path(__file__).parents[2]
    / "proxy"
    / "tests"
    / "unit"
    / "fixtures"
    / "console_config.json"
)
PARQUET = str(FILES / "people_partition_10.parquet")
ROUTED_TABLE = "p.d.routed"
FULL: Final = TableState(strategy=Strategy.FULL, signature="s")
POSTGRES_HOST = "127.0.0.1"
POSTGRES_PORT = environ["TEST_POSTGRES_PORT"]
POSTGRES_URL = f"postgresql://test:test@{POSTGRES_HOST}:{POSTGRES_PORT}"
WORKER = environ.get("PYTEST_XDIST_WORKER")
DUCKDB_DATABASE_SETTINGS = (
    "duckdb.unsafe_allow_execution_inside_functions",
    "duckdb.unsafe_allow_mixed_transactions",
)
TEST_DATABASE = "test_template" if WORKER is None else f"test_{WORKER}"
S3_ENDPOINT = environ["TEST_S3_ENDPOINT"]
S3_ACCESS_KEY = environ["TEST_S3_ACCESS_KEY"]
S3_SECRET_KEY = environ["TEST_S3_SECRET_KEY"]
S3_SECRET_INPUTS = {
    "endpoint": S3_ENDPOINT,
    "access_key": S3_ACCESS_KEY,
    "secret_key": S3_SECRET_KEY,
}


REQUEST_ROLES: Final = ("dp_t_api", "dp_t_policy", "dp_t_admin")


SIGNER: Final = "signer_t"

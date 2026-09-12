"""DuckDB connection factory and connection protocol."""

import duckdb
from duckdb import DuckDBPyConnection
from psycopg.sql import Literal

from .settings import settings
from .templates import execute_sql


def connect() -> DuckDBPyConnection:
    """Create an in-memory DuckDB connection with all extensions and secrets loaded."""
    conn = duckdb.connect()

    execute_sql(
        conn,
        "duckdb/setup",
        mapping={
            "key_id": Literal(settings.S3_ACCESS_KEY),
            "secret_key": Literal(settings.S3_SECRET_KEY),
            "endpoint": Literal(settings.S3_ENDPOINT),
            "use_ssl": "true" if settings.S3_USE_SSL else "false",
        },
    )

    return conn

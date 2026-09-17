"""DuckDB connection factory and connection protocol."""

import duckdb
from duckdb import DuckDBPyConnection
from psycopg.sql import Literal

from .executor import execute_sql
from .settings import settings


async def connect_duckdb() -> DuckDBPyConnection:
    """Create an in-memory DuckDB connection with all extensions and secrets loaded."""
    conn = duckdb.connect()

    await execute_sql(
        conn,
        "duckdb/setup",
        mapping={
            "s3_key_id": Literal(settings.S3_ACCESS_KEY),
            "s3_secret_key": Literal(settings.S3_SECRET_KEY),
            "s3_endpoint": Literal(settings.S3_ENDPOINT),
            "s3_use_ssl": "true" if settings.S3_USE_SSL else "false",
        },
    )

    return conn

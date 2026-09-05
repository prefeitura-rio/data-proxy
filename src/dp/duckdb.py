"""DuckDB connection factory and connection protocol."""

import duckdb
from duckdb import DuckDBPyConnection
from psycopg.sql import Literal

from .settings import settings
from .templates import render_template


def connect() -> DuckDBPyConnection:
    """Create an in-memory DuckDB connection with all extensions and secrets loaded."""
    conn = duckdb.connect()

    conn.execute(
        render_template(
            path="duckdb/setup",
            mapping={
                "key_id": Literal(settings.GCS_KEY_ID),
                "secret_key": Literal(settings.GCS_SECRET_KEY),
                "endpoint": Literal(settings.GCS_ENDPOINT),
                "use_ssl": "true" if settings.GCS_USE_SSL else "false",
            },
        )
    )

    return conn

"""DuckDB connection factory and connection protocol."""

from typing import Protocol

import duckdb

from .settings import settings
from .templates import load_template


class DBConnection(Protocol):
    """Structural interface for the DuckDB methods used by the sync pipeline."""

    def execute(self, query: str, parameters: object = None) -> DBConnection: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...
    def __enter__(self) -> DBConnection: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


def connect() -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection with all extensions and secrets loaded."""
    conn = duckdb.connect()

    conn.execute(
        load_template(
            "duckdb/setup",
            {
                "key_id": settings.GCS_KEY_ID,
                "secret_key": settings.GCS_SECRET_KEY,
                "endpoint": settings.GCS_ENDPOINT,
                "use_ssl": settings.GCS_USE_SSL,
            },
        )
    )

    return conn

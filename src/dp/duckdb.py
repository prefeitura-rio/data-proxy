"""DuckDB connection factory and connection protocol."""

from typing import Protocol

import duckdb


class DBConnection(Protocol):
    """Structural interface for the DuckDB methods used by the sync pipeline."""

    def execute(self, query: str, parameters: object = None) -> DBConnection: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...
    def __enter__(self) -> DBConnection: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


def connect(extensions: list[str] | None = None) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection, loading requested extensions."""
    conn = duckdb.connect()

    for ext in extensions or []:
        conn.execute(f"INSTALL {ext}")
        conn.execute(f"LOAD {ext}")

    return conn

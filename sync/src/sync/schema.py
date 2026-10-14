"""PostgreSQL schema initialization and PostgREST reload operations."""

from psycopg.sql import Identifier

from .settings import settings


def schema() -> Identifier:
    """Return the application state schema as a SQL identifier."""
    return Identifier(settings.DBOS_APP_SCHEMA)

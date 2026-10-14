"""Create the engine that the console uses for its own tables."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_engine(url: str) -> AsyncEngine:
    """Return an async engine for a PostgreSQL address, with a small pool.

    The pool opens connections only when a request needs one. It never holds
    more than four, because each one occupies a server connection of the pooler.
    """
    return create_async_engine(
        make_url(url).set(drivername="postgresql+psycopg"),
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
    )

"""Tests for `Settings.make_redis`'s URL field parsing."""

from dp.settings import Settings


def test_make_redis_parses_default_db() -> None:
    """A URL with no path segment defaults to database 0."""
    redis = Settings(REDIS_URL="redis://localhost:6379").make_redis()  # pyright: ignore[reportArgumentType]

    assert redis.connection_pool.connection_kwargs["db"] == 0


def test_make_redis_parses_explicit_db() -> None:
    """A URL with an explicit path segment uses it as the database index."""
    redis = Settings(REDIS_URL="redis://localhost:6379/3").make_redis()  # pyright: ignore[reportArgumentType]

    assert redis.connection_pool.connection_kwargs["db"] == 3


def test_make_redis_detects_tls_scheme() -> None:
    """A `rediss://` scheme enables TLS on the client."""
    redis = Settings(REDIS_URL="rediss://localhost:6379/0").make_redis()  # pyright: ignore[reportArgumentType]

    assert redis.connection_pool.connection_class.__name__ == "SSLConnection"


def test_make_redis_uses_host_port_and_credentials() -> None:
    """Host, port, username, and password are forwarded from the parsed URL."""
    redis = Settings(REDIS_URL="redis://user:secret@example.com:6380/1").make_redis()  # pyright: ignore[reportArgumentType]
    kwargs = redis.connection_pool.connection_kwargs

    assert kwargs["host"] == "example.com"
    assert kwargs["port"] == 6380
    assert kwargs["username"] == "user"
    assert kwargs["password"] == "secret"  # noqa: S105

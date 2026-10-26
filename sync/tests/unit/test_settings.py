from typing import Literal

import pytest
from pydantic import ValidationError

from sync.settings import Settings


class TestSnapshotExpiration:
    """DUCKLAKE_SNAPSHOT_EXPIRATION is a number of days, and nothing else."""

    @pytest.mark.parametrize("value", ["7d", "30d", "1d"])
    def test_accepts_a_positive_number_of_days(self, value: str) -> None:
        """Keep the text that DuckLake reads."""
        assert (
            value
            == Settings(DUCKLAKE_SNAPSHOT_EXPIRATION=value).DUCKLAKE_SNAPSHOT_EXPIRATION
        )

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("7", id="no-unit"),
            pytest.param("7h", id="hours"),
            pytest.param("0d", id="zero"),
            pytest.param("d", id="no-number"),
            pytest.param("-3d", id="negative"),
            pytest.param("1.5d", id="fraction"),
        ],
    )
    def test_refuses_anything_else(self, value: str) -> None:
        """Name the setting and the form that it needs."""
        with pytest.raises(ValidationError, match="must be like '7d'"):
            Settings(DUCKLAKE_SNAPSHOT_EXPIRATION=value)


class TestDumpQueueLimiter:
    """The rate limit of the dump queue is off at zero."""

    def test_has_no_limiter_by_default(self) -> None:
        """Let the queue run as fast as its workers go."""
        assert Settings().dump_queue_limiter is None

    def test_limits_the_dumps_for_each_minute(self) -> None:
        """Give DBOS the number and a period of 60 seconds."""
        assert Settings(DUMP_QUEUE_RATE_LIMIT=5).dump_queue_limiter == {
            "limit": 5,
            "period": 60.0,
        }


class TestRedisClient:
    """Settings.redis() builds a client for the read or the write address."""

    @staticmethod
    def kwargs(
        settings: Settings,
        *,
        role: Literal["read", "write"] = "write",
        db: int | None = None,
    ) -> dict[str, object]:
        """Return the connection arguments of the client that the settings build."""
        client = settings.redis(db=db, role=role)

        return dict(client.connection_pool.connection_kwargs)

    def test_takes_the_host_the_port_the_login_and_the_database_of_the_address(
        self,
    ) -> None:
        """Read each part of the URL."""
        settings = Settings(REDIS_WRITE="redis://ana:secret@cache.test:6380/3")

        found = self.kwargs(settings)

        assert (
            found["host"],
            found["port"],
            found["username"],
            found["password"],
            found["db"],
        ) == (
            "cache.test",
            6380,
            "ana",
            "secret",
            3,
        )

    def test_uses_the_read_address_for_a_reader(self) -> None:
        """Pick the address by role."""
        settings = Settings(
            REDIS_READ="redis://replica.test:6379/1",
            REDIS_WRITE="redis://primary.test:6379/0",
        )

        assert self.kwargs(settings, role="read")["host"] == "replica.test"
        assert self.kwargs(settings)["host"] == "primary.test"

    def test_an_explicit_database_wins_over_the_one_of_the_address(self) -> None:
        """Let the cache use its own database on the same server."""
        settings = Settings(REDIS_WRITE="redis://cache.test:6379/3")

        assert self.kwargs(settings, db=7)["db"] == 7

    def test_asks_for_a_tls_connection_only_for_the_rediss_scheme(self) -> None:
        """Encrypt the connection when the address says so."""
        plain = Settings(REDIS_WRITE="redis://cache.test:6379/0").redis()
        secure = Settings(REDIS_WRITE="rediss://cache.test:6379/0").redis()

        assert plain.connection_pool.connection_class.__name__ == "Connection"
        assert secure.connection_pool.connection_class.__name__ == "SSLConnection"

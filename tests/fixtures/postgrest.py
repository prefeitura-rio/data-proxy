"""A real PostgREST service for HTTP integration tests."""

from collections.abc import Iterator
from time import monotonic, sleep
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from tests.constants import POSTGREST_AUTH, TEST_SQL_DIR
from tests.fixtures.types import Postgrest


@pytest.fixture(scope="session")
def postgrest(
    postgres_container: PostgresContainer,
    container_network: Network,
) -> Iterator[Postgrest]:
    """Provide PostgREST backed by the shared PostgreSQL test database."""
    dsn_parts = urlsplit(postgres_container.get_connection_url(driver=None))
    dsn = urlunsplit(dsn_parts._replace(path="/test_template"))
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute((TEST_SQL_DIR / "postgres/pushdown.sql").read_bytes())

    container = (
        DockerContainer("postgrest/postgrest:v16.1")
        .with_env("PGRST_DB_URI", "postgres://test:test@postgres:5432/test_template")
        .with_env("PGRST_DB_SCHEMAS", "pushdown")
        .with_env("PGRST_DB_ANON_ROLE", "anon")
        .with_env("PGRST_JWT_SECRET", POSTGREST_AUTH)
        .with_env("PGRST_DB_PREPARED_STATEMENTS", "false")
        .with_env("PGRST_DB_CHANNEL_ENABLED", "false")
        .with_network(container_network)
        .with_exposed_ports(3000)
    )
    container.start()
    endpoint = (
        f"http://{container.get_container_host_ip()}:{container.get_exposed_port(3000)}"
    )
    deadline = monotonic() + 20
    while monotonic() < deadline:
        try:
            with urlopen(endpoint + "/rpc/items", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    break
        except OSError:
            sleep(0.1)
    else:
        container.stop()
        raise RuntimeError("PostgREST did not become ready within 20 seconds")

    try:
        yield Postgrest(endpoint)
    finally:
        container.stop()

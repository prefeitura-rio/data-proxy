"""BigQuery client lifecycle helpers."""

from collections.abc import Callable, Generator
from contextlib import contextmanager

from google.cloud.bigquery import Client


@contextmanager
def bigquery_clients() -> Generator[Callable[[str], Client]]:
    """Yield a per-project client getter and close clients on exit."""
    clients: dict[str, Client] = {}

    def get_client(project: str) -> Client:
        client = clients.get(project)

        if client is None:
            client = Client(project=project)
            clients[project] = client

        return client

    try:
        yield get_client
    finally:
        for client in clients.values():
            client.close()

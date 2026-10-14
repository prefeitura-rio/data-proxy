"""The part of the DBOS client that the API uses."""

from typing import Protocol


class WorkflowClient(Protocol):
    """The DBOS client calls of the API. Tests use a fake that implements them."""

    async def check_connection_async(self) -> None:
        """Raise when the DBOS system database is unreachable."""
        ...

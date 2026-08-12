"""FastStream error handling for finite Kubernetes Jobs."""

from typing import NoReturn

from faststream.exceptions import StopApplication


async def stop_on_error(error: Exception) -> NoReturn:
    """Stop the process with a failure status after a subscriber error."""
    raise StopApplication(1) from error

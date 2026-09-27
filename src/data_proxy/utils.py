"""Shared async helpers for the synchronization service."""

from collections.abc import Awaitable, Callable

from tenacity import retry, stop_after_delay, wait_fixed


async def wait_for(
    check: Callable[[], Awaitable[None]],
    *,
    timeout: float,
    interval: float,
    message: str,
) -> None:
    """Retry an async readiness check until it succeeds or times out."""

    @retry(
        stop=stop_after_delay(timeout),
        wait=wait_fixed(interval),
        reraise=True,
    )
    async def attempt() -> None:
        await check()

    try:
        await attempt()
    except Exception as error:
        raise TimeoutError(message) from error

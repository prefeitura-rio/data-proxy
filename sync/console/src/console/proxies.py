"""Tell the proxies that the sync config changed."""

import asyncio
import logging
import socket

from httpx2 import URL, AsyncClient

logger = logging.getLogger(__name__)


async def notify(url: str | None) -> None:
    """Call the reload endpoint of every proxy that the host name of the URL resolves to.

    A proxy that can't be reached is skipped. It catches up at its next periodic check.
    """
    if url is None:
        return
    target = URL(url)
    found = await asyncio.get_running_loop().getaddrinfo(
        target.host, target.port or 80, type=socket.SOCK_STREAM
    )
    addresses = {str(info[4][0]) for info in found}
    async with AsyncClient(timeout=5) as http:
        results = await asyncio.gather(
            *(http.post(str(target.copy_with(host=address))) for address in addresses),
            return_exceptions=True,
        )
    for address, result in zip(addresses, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Proxy reload failed: address=%s error=%s", address, result)

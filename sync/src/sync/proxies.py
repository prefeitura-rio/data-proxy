"""Ask the proxy pods to read the configuration again."""

from asyncio import get_running_loop
from socket import AF_INET, SOCK_STREAM
from urllib.parse import urlsplit

from httpx2 import AsyncClient, HTTPError


class ProxyReloadError(Exception):
    """One or more proxy pods did not take the reload request."""


async def proxy_addresses(url: str) -> list[str]:
    """Resolve the host of the URL to the sorted IPv4 address of each pod behind it."""
    parts = urlsplit(url)
    found = await get_running_loop().getaddrinfo(
        parts.hostname, parts.port or 80, family=AF_INET, type=SOCK_STREAM
    )

    return sorted({str(address[4][0]) for address in found})


async def reload_proxies(client: AsyncClient, url: str, addresses: list[str]) -> None:
    """Send the reload request to every address and raise if any pod failed.

    Every pod gets its request even when an earlier one failed, so a retry only repeats
    a request that is safe to repeat.
    """
    parts = urlsplit(url)
    path = parts.path or "/"
    failed: list[str] = []

    for address in addresses:
        try:
            response = await client.post(
                f"{parts.scheme}://{address}:{parts.port or 80}{path}"
            )
            response.raise_for_status()
        except HTTPError, OSError:
            failed.append(address)

    if failed:
        raise ProxyReloadError(f"Proxy pods did not reload: {', '.join(failed)}")

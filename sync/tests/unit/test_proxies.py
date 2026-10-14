import pytest
from httpx2 import AsyncClient, MockTransport, Request, Response

from sync.proxies import ProxyReloadError, proxy_addresses, reload_proxies

URL = "http://proxy-admin.ns.svc:8081/reload"


def client_answering(statuses: dict[str, int], seen: list[Request]) -> AsyncClient:
    """Build a client whose pods answer with the given status, by address."""

    def answer(request: Request) -> Response:
        seen.append(request)
        return Response(statuses.get(request.url.host, 200), json={"changed": True})

    return AsyncClient(transport=MockTransport(answer))


class TestReloadProxies:
    """The engine asks every proxy pod to read the configuration again."""

    async def test_posts_to_the_path_and_port_of_every_pod(self) -> None:
        """Each address gets one POST, with the port and the path of the URL."""
        seen: list[Request] = []
        async with client_answering({}, seen) as client:
            await reload_proxies(client, URL, ["10.0.0.1", "10.0.0.2"])

        assert [(r.method, str(r.url)) for r in seen] == [
            ("POST", "http://10.0.0.1:8081/reload"),
            ("POST", "http://10.0.0.2:8081/reload"),
        ]

    async def test_asks_the_other_pods_when_one_fails_and_names_the_failed_one(
        self,
    ) -> None:
        """One bad pod doesn't keep the others on the old settings, and DBOS retries the step."""
        seen: list[Request] = []
        async with client_answering({"10.0.0.2": 502}, seen) as client:
            with pytest.raises(ProxyReloadError, match=r"10\.0\.0\.2"):
                await reload_proxies(client, URL, ["10.0.0.1", "10.0.0.2", "10.0.0.3"])

        assert [r.url.host for r in seen] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    async def test_a_pod_that_cannot_be_reached_counts_as_failed(self) -> None:
        """A refused connection is a failure like a bad answer."""

        def refuse(request: Request) -> Response:
            raise ConnectionError(request.url.host)

        async with AsyncClient(transport=MockTransport(refuse)) as client:
            with pytest.raises(ProxyReloadError, match=r"10\.0\.0\.1"):
                await reload_proxies(client, URL, ["10.0.0.1"])

    async def test_nothing_to_do_without_pods(self) -> None:
        """No proxy pod means no request."""
        seen: list[Request] = []
        async with client_answering({}, seen) as client:
            await reload_proxies(client, URL, [])

        assert seen == []


class TestProxyAddresses:
    """The headless service name resolves to the address of every pod."""

    async def test_resolves_the_host_of_the_url(self) -> None:
        """An address in the URL resolves to itself, once."""
        assert await proxy_addresses("http://127.0.0.1:8081/reload") == ["127.0.0.1"]

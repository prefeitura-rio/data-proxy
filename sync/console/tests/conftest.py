from collections.abc import AsyncGenerator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from console.database import create_engine
from console.dependencies import get_catalog, get_client, get_engine
from console.main import app
from console.settings import settings
from helpers import FakeClient, ProxyAdmin


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    """Connect to the test database, with empty console tables."""
    engine = create_engine(settings.PG_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE data_proxy.schemas, data_proxy.config_revisions RESTART IDENTITY"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def fake() -> FakeClient:
    """Give the tests the one fake DBOS client that the app uses, to read what it received."""
    return FakeClient()


@pytest.fixture
async def api(engine: AsyncEngine, fake: FakeClient) -> AsyncGenerator[AsyncClient]:
    """Call the console app over HTTP, with the test database and a fake DBOS client."""
    app.dependency_overrides[get_client] = lambda: fake
    app.dependency_overrides.setdefault(get_catalog, lambda: None)
    app.dependency_overrides[get_engine] = lambda: engine
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def proxy_admin(monkeypatch: pytest.MonkeyPatch) -> Iterator[ProxyAdmin]:
    """Serve a proxy admin endpoint on a loopback port, and point the console at it."""
    state = ProxyAdmin(url="")

    class Handler(BaseHTTPRequestHandler):
        """Answer every POST and write down its path."""

        def do_POST(self) -> None:
            """Remember the path and answer with an empty JSON object."""
            state.calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}/reload"
    monkeypatch.setattr(settings, "PROXY_ADMIN_URL", state.url)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()

from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import override

import pytest


@dataclass
class Console:
    """A console that serves sync.json from memory, over real HTTP."""

    url: str
    body: str = '{"schemas": {}}'
    etag: str = '"1"'
    status: int = 200


@pytest.fixture
def console() -> Iterator[Console]:
    """Serve sync.json on a loopback port, so a test can change what the console says."""
    state = Console(url="")

    class Handler(BaseHTTPRequestHandler):
        """Answer every GET with the current state of the console."""

        def do_GET(self) -> None:
            """Send the status, the ETag and the body of the state."""
            payload = state.body.encode()
            self.send_response(state.status)
            self.send_header("ETag", state.etag)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        @override
        def log_message(self, format: str, *args: object) -> None:
            """Keep the test output quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}/config/sync.json"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()

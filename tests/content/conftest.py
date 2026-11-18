"""Loopback-only HTTP fixtures for content tests."""

import base64
import hashlib
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aKX0AAAAASUVORK5CYII="
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack_entry(url: str, data: bytes = b"pack data", **overrides: object) -> dict:
    return {
        "id": "example",
        "kind": "wallpaper",
        "title": "Example",
        "description": "An example wallpaper.",
        "author": "Test creator",
        "license": "CC0-1.0",
        "attribution": "Wallpaper by Test creator.",
        "source_url": f"{url}/source",
        "version": "1.0.0",
        "download_url": f"{url}/asset",
        "size_bytes": len(data),
        "sha256": digest(data),
        "previews": [{"url": f"{url}/preview", "sha256": digest(PNG), "size_bytes": len(PNG)}],
        "min_app_version": "5.5.0",
        **overrides,
    }


def send(
    handler: BaseHTTPRequestHandler, body: bytes = b"", status: int = 200, **headers: str
) -> None:
    handler.send_response(status)
    if "Content-Length" not in headers:
        handler.send_header("Content-Length", str(len(body)))
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    if body:
        handler.wfile.write(body)


class FakeHTTP:
    def __init__(self) -> None:
        self.routes: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler) -> None:
                self.calls.append((handler.path, dict(handler.headers)))
                try:
                    if route := self.routes.get(handler.path):
                        route(handler)
                    else:
                        send(handler, status=404)
                except (BrokenPipeError, ConnectionResetError):
                    # A cancelled client deliberately closes the connection.
                    pass

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHTTP]:
    monkeypatch.delenv("EDOPRO_CONTENT_MANIFEST_URL", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = FakeHTTP()
    try:
        yield server
    finally:
        server.close()

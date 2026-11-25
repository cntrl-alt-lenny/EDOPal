"""Loopback TCP passthrough. Observation can fall behind without blocking a duel."""

from __future__ import annotations

import asyncio
import socket
import threading
from contextlib import suppress
from copy import deepcopy

from .protocol import Session

DEFAULT_PORT = 17912


def bind_loopback(port=DEFAULT_PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen()
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def validate_target(host, port, listen_port):
    if port == listen_port:
        endpoints = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if any(address[0] in ("127.0.0.1", "::ffff:127.0.0.1", "0.0.0.0") for *_, address in endpoints):
            raise ValueError("Select an upstream server other than the relay itself.")


class _Tap:
    def __init__(self, session, publish):
        self.session = session
        self.publish = publish
        self.queue = asyncio.Queue(maxsize=32)
        self.disabled = False
        self.worker = asyncio.create_task(self._observe())

    def offer(self, direction, data):
        if self.disabled:
            return
        try:
            self.queue.put_nowait((direction, data))
        except asyncio.QueueFull:
            self.disabled = True
            self.worker.cancel()
            self.publish("Tracking paused · missed traffic", None)

    def _parse(self, direction, data):
        revision = self.session.revision
        self.session.feed(direction, data)
        if revision != self.session.revision:
            return self.session.status, deepcopy(self.session.deck)
        return None

    async def _observe(self):
        try:
            while True:
                item = await self.queue.get()
                if result := await asyncio.to_thread(self._parse, *item):
                    self.publish(*result)
        except Exception:
            # A decoder fault must never interrupt either TCP stream.
            self.disabled = True
            self.publish("Tracking paused · unreadable traffic", None)

    async def close(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)


class Relay:
    def __init__(self, host, port, publish, card_types=None):
        self.host, self.port = host, port
        self.publish = publish
        self.card_types = card_types
        self.clients = {}
        self.tracked = None
        self.server = None

    async def start(self, sock):
        if sock.getsockname()[1] == self.port:
            await asyncio.to_thread(validate_target, self.host, self.port, self.port)
        self.server = await asyncio.start_server(self._accept, sock=sock)

    def _accept(self, reader, writer):
        # Register synchronously so close() also owns clients accepted in this loop tick.
        track = self.tracked is None
        task = asyncio.create_task(self._connection(reader, writer, track))
        if track:
            self.tracked = task
        self.clients[task] = writer
        task.add_done_callback(self.clients.pop)

    async def _connection(self, reader, writer, track):
        upstream = tap = None
        pumps = []
        status = "Duel disconnected · ready to reconnect"
        try:
            remote, upstream = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 10)
            if track:
                tap = _Tap(Session(self.card_types), self.publish)
                self.publish("Connected · waiting for the room", None)

            async def pump(source, dest, from_client):
                while data := await source.read(32768):
                    dest.write(data)
                    if tap:
                        tap.offer(from_client, data)
                    await dest.drain()
                dest.write_eof()

            pumps = [
                asyncio.create_task(pump(reader, upstream, True)),
                asyncio.create_task(pump(remote, writer, False)),
            ]
            done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            # Preserve the peer's final response on a TCP half-close.
            if pending:
                await asyncio.wait_for(asyncio.gather(*pending), 10)
        except (OSError, TimeoutError) as exc:
            if upstream is None:
                status = f"Upstream unavailable · {exc}"
        finally:
            for stream in (writer, upstream):
                if stream:
                    stream.close()
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if tap:
                await tap.close()
            if track:
                self.tracked = None
                self.publish(status, None)
            for stream in (writer, upstream):
                if stream:
                    with suppress(OSError, TimeoutError):
                        await asyncio.wait_for(stream.wait_closed(), 1)

    async def close(self):
        if self.server:
            self.server.close()
            # On Python 3.12+, wait_closed also waits for active connections.
        clients = tuple(self.clients)
        for task in clients:
            self.clients[task].close()
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        if self.server:
            await self.server.wait_closed()


class RelayService:
    """Own one socket loop independently of Qt. The GUI polls immutable-after-publish snapshots."""

    def __init__(self):
        self.stop_requested = threading.Event()
        self.latest = ("Relay stopped", None)
        self.thread = None

    def start(self, sock, server, card_types):
        def publish(status, deck):
            self.latest = (status, deck)

        async def run():
            relay = Relay(server.address, server.duelport, publish, card_types)
            try:
                await relay.start(sock)
                publish("Listening · choose EDOPal Relay in EDOPro", None)
                while not self.stop_requested.is_set():
                    await asyncio.sleep(0.05)
            finally:
                await relay.close()

        def background():
            try:
                asyncio.run(run())
            except (OSError, ValueError) as exc:
                publish(str(exc), None)
            finally:
                sock.close()

        self.thread = threading.Thread(target=background, name="EDOPal deck relay", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_requested.set()

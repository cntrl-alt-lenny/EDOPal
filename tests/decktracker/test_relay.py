import asyncio
import struct
import threading

from edopal.plugins.decktracker.protocol import Session
from edopal.plugins.decktracker.relay import Relay, bind_loopback

from .test_protocol import draw, frame, host, join, start, submit


def test_passthrough_half_close_disconnect_and_reconnect():
    async def run():
        client_bytes = join() + frame(0x16, b"private chat") + submit()
        server_bytes = host() + frame(0x15) + start(1) + draw(1, 11)
        final = frame(0x30, bytes(range(256)) * 200)
        observations = asyncio.Queue()

        async def endpoint(reader, writer):
            try:
                assert await reader.readexactly(len(client_bytes)) == client_bytes
                writer.write(server_bytes)
                await writer.drain()
                assert await reader.read() == b""
                writer.write(final)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with await asyncio.start_server(endpoint, "127.0.0.1", 0) as upstream:
            relay = Relay("127.0.0.1", upstream.sockets[0].getsockname()[1], lambda *args: observations.put_nowait(args))
            sock = bind_loopback(0)
            await relay.start(sock)
            try:
                assert sock.getsockname()[0] == "127.0.0.1"
                for _ in range(2):
                    reader, writer = await asyncio.open_connection(*sock.getsockname())
                    for chunk in (client_bytes[:1], client_bytes[1:5], client_bytes[5:]):
                        writer.write(chunk)
                        await writer.drain()
                    assert await reader.readexactly(len(server_bytes)) == server_bytes
                    while True:
                        status, deck = await observations.get()
                        if status == "Live" and deck.total == 3:
                            break
                    assert deck.remaining == {11: 1, 22: 1, 33: 1}
                    writer.write_eof()
                    assert await reader.read() == final
                    writer.close()
                    await writer.wait_closed()
                    while "disconnected" not in (await observations.get())[0]:
                        pass
            finally:
                await relay.close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_blocked_decoder_cannot_hold_up_duel(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked(self, direction, data):
        entered.set()
        release.wait(8)

    monkeypatch.setattr(Session, "feed", blocked)

    async def run():
        statuses = []

        async def echo(reader, writer):
            try:
                while chunk := await reader.read(32768):
                    writer.write(chunk)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with await asyncio.start_server(echo, "127.0.0.1", 0) as upstream:
            relay = Relay("127.0.0.1", upstream.sockets[0].getsockname()[1], lambda s, d: statuses.append(s))
            sock = bind_loopback(0)
            await relay.start(sock)
            reader, writer = await asyncio.open_connection(*sock.getsockname())
            try:
                for i in range(45):
                    payload = struct.pack("<I", i) * 9000
                    writer.write(payload)
                    assert await reader.readexactly(len(payload)) == payload
                assert entered.is_set()
                assert any("missed traffic" in status for status in statuses)
            finally:
                release.set()
                writer.close()
                await writer.wait_closed()
                await relay.close()

    asyncio.run(asyncio.wait_for(run(), 4))


def test_upstream_failure_recovers_on_next_connection():
    async def run():
        # A bound socket reserves the endpoint without accepting connections until listen().
        import socket

        with socket.socket() as remote_sock:
            remote_sock.bind(("127.0.0.1", 0))
            notices = asyncio.Queue()
            relay = Relay("127.0.0.1", remote_sock.getsockname()[1], lambda s, d: notices.put_nowait(s))
            sock = bind_loopback(0)
            await relay.start(sock)
            try:
                reader, writer = await asyncio.open_connection(*sock.getsockname())
                assert await reader.read() == b""
                assert "Upstream unavailable" in await notices.get()
                writer.close()
                await writer.wait_closed()

                async def respond(reader, writer):
                    writer.write(b"restored")
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()

                async with await asyncio.start_server(respond, sock=remote_sock):
                    reader, writer = await asyncio.open_connection(*sock.getsockname())
                    assert await reader.read() == b"restored"
                    writer.close()
                    await writer.wait_closed()
            finally:
                await relay.close()

    asyncio.run(asyncio.wait_for(run(), 4))

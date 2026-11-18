"""Read common album/title tags as optional hints, bounded independently of audio size."""

from __future__ import annotations

import struct
from pathlib import Path

_LIMIT = 512 * 1024


def _comments(data: bytes) -> list[str]:
    offset = 4 + int.from_bytes(data[:4], "little")
    count = int.from_bytes(data[offset : offset + 4], "little")
    offset += 4
    result = []
    for _ in range(min(count, 4096)):
        if offset + 4 > len(data):
            break
        size = int.from_bytes(data[offset : offset + 4], "little")
        offset += 4
        if offset + size > len(data):
            break
        key, _, value = data[offset : offset + size].partition(b"=")
        if key.upper() in {b"ALBUM", b"TITLE"}:
            result.append(value.decode("utf-8", "replace"))
        offset += size
    return result


def _syncsafe(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value << 7) + (byte & 127)
    return value


def tags(path: Path) -> list[str]:
    with path.open("rb") as stream:
        head = stream.read(10)
        if head.startswith(b"fLaC"):
            stream.seek(4)
            for _ in range(128):
                block = stream.read(4)
                if len(block) < 4:
                    break
                size = int.from_bytes(block[1:], "big")
                if block[0] & 127 == 4 and size <= _LIMIT:
                    return _comments(stream.read(size))
                if block[0] & 128:
                    break
                stream.seek(size, 1)
        elif head.startswith(b"OggS"):
            # Reassemble the first Vorbis packets across page boundaries.
            stream.seek(0)
            packet = bytearray()
            while stream.tell() < _LIMIT:
                page = stream.read(27)
                if len(page) != 27 or page[:4] != b"OggS":
                    break
                for length in stream.read(page[26]):
                    packet.extend(stream.read(length))
                    if length < 255:
                        if packet.startswith(b"\x03vorbis"):
                            return _comments(bytes(packet[7:]))
                        packet.clear()
        elif head.startswith(b"ID3") and head[3] in (3, 4):
            data = stream.read(min(_LIMIT, _syncsafe(head[6:10])))
            offset = 0
            result = []
            while offset + 10 <= len(data):
                frame = data[offset : offset + 10]
                size = _syncsafe(frame[4:8]) if head[3] == 4 else struct.unpack(">I", frame[4:8])[0]
                offset += 10
                if not size or offset + size > len(data):
                    break
                if frame[:4] in (b"TALB", b"TIT2") and data[offset] < 4:
                    codec = ("latin1", "utf-16", "utf-16-be", "utf-8")[data[offset]]
                    result.append(
                        data[offset + 1 : offset + size].decode(codec, "replace").strip("\0")
                    )
                offset += size
            return result
        elif path.suffix.lower() == ".mp3" and stream.seek(0, 2) >= 128:
            stream.seek(-128, 2)
            tag = stream.read(128)
            if tag.startswith(b"TAG"):
                return [tag[a : a + 30].decode("latin1").strip("\0 ") for a in (63, 3)]
        elif head.startswith(b"RIFF"):
            stream.seek(12)
            for _ in range(256):
                chunk = stream.read(8)
                if len(chunk) < 8:
                    break
                size = int.from_bytes(chunk[4:], "little")
                if chunk[:4] == b"LIST" and size <= _LIMIT:
                    data = stream.read(size)
                    if data.startswith(b"INFO"):
                        result = []
                        offset = 4
                        while offset + 8 <= len(data):
                            key, length = struct.unpack_from("<4sI", data, offset)
                            offset += 8
                            if key in (b"IPRD", b"INAM"):
                                result.append(
                                    data[offset : offset + length]
                                    .decode("utf-8", "replace")
                                    .strip("\0")
                                )
                            offset += length + length % 2
                        return result
                    stream.seek(size % 2, 1)
                else:
                    stream.seek(size + size % 2, 1)
    return []

"""Bounded metadata reader for the EDOPro 41.0.2 replay layout.

See https://github.com/edo9300/edopro/blob/41.0.2/gframe/replay.h
and https://github.com/edo9300/edopro/blob/41.0.2/gframe/replay.cpp.
"""

import lzma
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

COMPRESSED = 0x01
TAG = 0x02
SINGLE = 0x08
NEW = 0x20
HAND_TEST = 0x40
DUEL_FLAGS_64 = 0x100
EXTENDED = 0x200
MSG_NEW_TURN = 40

_BASE = struct.Struct("<4s5I8s")
_EXTENSION = struct.Struct("<5Q")
_PACKET = struct.Struct("<BI")
MAX_FILE_BYTES = 32 * 1024**2
_MAX_PAYLOAD = 64 * 1024**2
_MAX_DICTIONARY = 32 * 1024**2
_MAX_PACKETS = 1_000_000
_MAX_PLAYERS_PER_SIDE = 16


class ReplayFormatError(ValueError):
    """Metadata could not be read within the supported format and limits."""


@dataclass(frozen=True)
class ReplayHeader:
    format: Literal["yrp1", "yrpX"]
    version: int
    flags: int
    timestamp: int
    payload_size: int
    revision: int | None

    @property
    def date(self) -> datetime | None:
        return datetime.fromtimestamp(self.timestamp, UTC) if self.timestamp else None

    @property
    def compressed(self) -> bool:
        return bool(self.flags & COMPRESSED)


@dataclass(frozen=True)
class ReplayDetails:
    header: ReplayHeader
    teams: tuple[tuple[str, ...], tuple[str, ...]]
    rule_flags: int
    turns: int | None


def parse_header(data: bytes) -> ReplayHeader:
    """Read the 32-byte base header and, when flagged, its 40-byte extension."""
    if len(data) < _BASE.size:
        raise ReplayFormatError("The replay header is incomplete.")
    magic, version, flags, timestamp, size, _, _ = _BASE.unpack_from(data)
    if magic not in (b"yrp1", b"yrpX"):
        raise ReplayFormatError("The file has an unknown replay signature.")
    revision = None
    if flags & EXTENDED:
        if len(data) < _BASE.size + _EXTENSION.size:
            raise ReplayFormatError("The extended replay header is incomplete.")
        revision = _EXTENSION.unpack_from(data, _BASE.size)[0]
        if revision > 1:
            raise ReplayFormatError("The replay uses a newer header revision.")
    return ReplayHeader(
        "yrpX" if magic == b"yrpX" else "yrp1", version, flags, timestamp, size, revision
    )


def _payload(data: bytes, header: ReplayHeader) -> bytes:
    start = _BASE.size + (_EXTENSION.size if header.revision is not None else 0)
    payload = data[start:]
    if not header.compressed:
        if header.payload_size and header.payload_size != len(payload):
            raise ReplayFormatError("The replay payload length does not match its header.")
        return payload

    props = _BASE.unpack_from(data)[-1]
    prop = props[0]
    dictionary = int.from_bytes(props[1:5], "little")
    if not 0 < header.payload_size <= _MAX_PAYLOAD or dictionary > _MAX_DICTIONARY:
        raise ReplayFormatError("The replay exceeds the decompression limits.")
    if prop >= 225:
        raise ReplayFormatError("The replay has invalid compression settings.")
    filters = [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": max(4096, dictionary),
            "lc": prop % 9,
            "lp": (prop // 9) % 5,
            "pb": prop // 45,
        }
    ]
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
        decoded = decoder.decompress(payload, max_length=header.payload_size)
    except (lzma.LZMAError, ValueError) as exc:
        raise ReplayFormatError("The compressed replay is damaged.") from exc
    # The upstream LZMA SDK can omit the end marker and relies on the declared size.
    if len(decoded) != header.payload_size or decoder.unused_data:
        raise ReplayFormatError(
            "The compressed replay payload is incomplete or has the wrong length."
        )
    return decoded


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.position

    def skip(self, size: int) -> int:
        if size > self.remaining:
            raise ReplayFormatError("The replay payload is incomplete.")
        start = self.position
        self.position += size
        return start

    def integer(self, size: int) -> int:
        offset = self.skip(size)
        return int.from_bytes(self.data[offset : offset + size], "little")

    def name(self) -> str:
        offset = self.skip(40)
        name = self.data[offset : offset + 40].decode("utf-16-le", errors="replace")
        return name.split("\0", 1)[0]


def _team(reader: _Reader, flags: int) -> tuple[str, ...]:
    if flags & SINGLE:
        count = 1
    elif flags & NEW:
        count = reader.integer(4)
    else:
        count = 2 if flags & TAG else 1
    if not 1 <= count <= _MAX_PLAYERS_PER_SIDE:
        raise ReplayFormatError("The replay has an unsupported player count.")
    return tuple(reader.name() for _ in range(count))


def _count_turns(reader: _Reader) -> int | None:
    turns = 0
    for _ in range(_MAX_PACKETS):
        if not reader.remaining:
            return turns
        offset = reader.skip(_PACKET.size)
        message, size = _PACKET.unpack_from(reader.data, offset)
        reader.skip(size)
        turns += message == MSG_NEW_TURN
    return None


def _check_legacy_framing(reader: _Reader, flags: int, players: int) -> None:
    # Skip legacy deck lists, script name and responses without interpreting their contents.
    if flags & SINGLE:
        reader.skip(reader.integer(2))
    if not flags & SINGLE or flags & HAND_TEST:
        for _ in range(players * 2):
            reader.skip(reader.integer(4) * 4)
        if flags & NEW and not flags & HAND_TEST:
            reader.skip(reader.integer(4) * 4)
    for _ in range(_MAX_PACKETS):
        if not reader.remaining:
            return
        size = reader.integer(1)
        if not size:
            return
        reader.skip(size)
    raise ReplayFormatError("The legacy replay exceeds the response limit.")


def parse_replay(data: bytes) -> ReplayDetails:
    """Read teams and rule flags and count streamed turn markers within fixed limits."""
    if len(data) > MAX_FILE_BYTES:
        raise ReplayFormatError("The replay exceeds the file size limit.")
    header = parse_header(data)
    reader = _Reader(_payload(data, header))
    teams = (_team(reader, header.flags), _team(reader, header.flags))
    if header.format == "yrp1":
        reader.skip(12)
    rule_flags = reader.integer(8 if header.flags & DUEL_FLAGS_64 else 4)
    if header.format == "yrpX":
        if not reader.remaining:
            raise ReplayFormatError("The streamed replay has no duel packets.")
        turns = _count_turns(reader)
    else:
        _check_legacy_framing(reader, header.flags, sum(map(len, teams)))
        turns = None
    return ReplayDetails(header, teams, rule_flags, turns)


def read_replay(path: Path) -> ReplayDetails:
    """Read replay metadata from a file."""
    with path.open("rb") as stream:
        return parse_replay(stream.read(MAX_FILE_BYTES + 1))

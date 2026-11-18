import json
import lzma
import struct
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

import music_library
from replays import (
    AnnotationStore,
    ReplayFormatError,
    ReplayNotes,
    export_replay,
    find_last_replays,
    index_replays,
    parse_header,
    parse_replay,
    preserve_last_replay,
    search_replays,
)
from replays import shelf as engine


def name(value: str) -> bytes:
    return value.encode("utf-16-le").ljust(40, b"\x00")


def packet(message: int, payload: bytes) -> bytes:
    return struct.pack("<BI", message, len(payload)) + payload


def header(
    magic: bytes,
    flags: int,
    timestamp: int,
    size: int,
    props: bytes = bytes(8),
    revision: int = 1,
) -> bytes:
    base = struct.pack("<4s5I8s", magic, 720937, flags, timestamp, size, 0, props)
    return base + struct.pack("<5Q", revision, 4, 3, 2, 1) if flags & 512 else base


def streamed(
    *,
    compressed: bool = False,
    timestamp: int = 1_700_000_000,
    turns: int = 2,
    include_end_marker: bool = True,
) -> bytes:
    body = struct.pack("<I", 2) + name("Player 🐉") + name("Partner")
    body += struct.pack("<I", 1) + name("Rival")
    body += struct.pack("<Q", 0x100000001)
    body += packet(231, b"ignored inside this packet")
    body += packet(40, b"\x01") * turns
    if not compressed:
        return header(b"yrpX", 512 + 256 + 32, timestamp, 0) + body
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16, "lc": 3, "lp": 0, "pb": 2}]
    payload = lzma.compress(body, format=lzma.FORMAT_RAW, filters=filters)
    if not include_end_marker:
        # Find a prefix producing the full declared output with no end marker.
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
        for count, byte in enumerate(payload, 1):
            decoder.decompress(bytes([byte]))
            if not decoder.eof:
                candidate = payload[:count]
                if (
                    lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters).decompress(
                        candidate,
                        max_length=len(body),
                    )
                    == body
                ):
                    payload = candidate
                    break
        else:
            raise AssertionError("Missing a markerless LZMA fixture")
    props = b"\x5d" + struct.pack("<I", 1 << 16) + bytes(3)
    return header(b"yrpX", 512 + 256 + 32 + 1, timestamp, len(body), props) + payload


def legacy() -> bytes:
    body = name("A") + name("B") + name("C") + name("D")
    body += struct.pack("<4I", 8000, 5, 1, 0xABCDE)
    body += struct.pack("<8I", 0, 0, 0, 0, 0, 0, 0, 0) + b"\x01\x02"
    return header(b"yrp1", 2, 1, 0) + body


def put(root: Path, file: str, data: bytes) -> Path:
    output = root / "replay" / file
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(data)
    return output


@pytest.mark.parametrize("compressed,marker", [(False, False), (True, True), (True, False)])
def test_streamed_metadata(compressed: bool, marker: bool) -> None:
    result = parse_replay(streamed(compressed=compressed, include_end_marker=marker))
    assert result.header.format == "yrpX"
    assert result.header.version == 720937
    assert result.header.revision == 1
    assert result.header.compressed is compressed
    assert result.header.date == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert result.teams == (("Player 🐉", "Partner"), ("Rival",))
    assert result.rule_flags == 0x100000001
    assert result.turns == 2


def test_base_legacy_tag_replay_has_no_streamed_turn_count() -> None:
    result = parse_replay(legacy())
    assert result.header.format == "yrp1"
    assert result.header.revision is None
    assert result.teams == (("A", "B"), ("C", "D"))
    assert result.rule_flags == 0xABCDE
    assert result.turns is None


def test_single_player_uses_fixed_name_slots_and_skips_script() -> None:
    body = name("A") + name("") + struct.pack("<3IQ", 8000, 5, 1, 0xABCD)
    body += b"\x03\x00xyz\x01\x02"
    result = parse_replay(header(b"yrp1", 512 + 256 + 32 + 8, 0, 0) + body)
    assert result.teams == (("A",), ("",))
    assert result.header.date is None
    assert result.rule_flags == 0xABCD


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"yrpX",
        b"not a replay".ljust(80, b"!"),
        streamed()[:71],
        streamed()[:-1],
        legacy()[:-1],
        streamed(compressed=True)[:90],
        header(b"yrpX", 512, 1, 0, revision=2),
        header(b"yrpX", 512 + 32, 1, 0) + struct.pack("<I", 0xFFFFFFFF),
        header(b"yrpX", 1, 1, 0xFFFFFFFF),
        header(b"yrpX", 1, 1, 80, b"\x5d\x00\x00\x00\xff\x00\x00\x00"),
    ],
)
def test_truncated_unsupported_and_garbage_input(bad: bytes) -> None:
    with pytest.raises(ReplayFormatError):
        parse_replay(bad)


def test_annotations_survive_rename_and_index_search_sort(tmp_path: Path) -> None:
    store = AnnotationStore(tmp_path / "settings")
    folder = tmp_path / "replay"
    old = put(tmp_path, "old.yrpX", streamed(turns=5))
    put(tmp_path, "new.yrpX", streamed(timestamp=1_700_000_001, turns=2))
    put(tmp_path, "garbage.yrp", b"garbage")
    put(tmp_path, "other.txt", b"ignore me")
    assert store.all() == {}
    assert not store.path.exists()
    entry = next(entry for entry in index_replays(tmp_path) if entry.path == old)
    assert entry.replay_id is not None
    store.set(entry.replay_id, ReplayNotes("Review", "Check opening", ("goat", " test ", "goat")))
    old.rename(folder / "renamed.yrpx")
    entries = index_replays(tmp_path, annotations=store)
    assert len(entries) == 3
    (found,) = search_replays(entries, "OPENING rival Test rename")
    assert found.annotation == ReplayNotes("Review", "Check opening", ("goat", "test"))
    assert search_replays(entries)[0].path.name == "new.yrpX"
    assert search_replays(entries, sort="turns")[0].path.name == "renamed.yrpx"
    assert search_replays(entries, sort="turns", descending=False)[-1].error
    store.set(entry.replay_id, ReplayNotes())
    assert store.get(entry.replay_id) == ReplayNotes()


def test_index_reads_small_replays_without_reserving_the_format_limit(tmp_path: Path) -> None:
    data = streamed()
    path = put(tmp_path, "small.yrpX", data)
    with (
        path.open("rb") as stream,
        patch.object(stream, "read", wraps=stream.read) as read,
        patch.object(Path, "open", return_value=stream),
    ):
        (entry,) = index_replays(tmp_path)
    assert entry.details is not None
    read.assert_called_once_with(len(data) + 1)


def test_bad_payload_retains_verified_header_in_index(tmp_path: Path) -> None:
    path = put(tmp_path, "partial.yrpX", streamed()[:72])
    (entry,) = index_replays(tmp_path)
    assert entry.path == path
    assert entry.header == parse_header(path.read_bytes())
    assert entry.details is None
    assert entry.error


def test_preserve_only_after_stable_and_never_replace_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = streamed()
    source = put(tmp_path, "_LastReplay.yrpX", data)
    previous = put(tmp_path, "_LastReplay.yrp", legacy())
    assert set(find_last_replays(tmp_path)) == {source, previous}
    changed = streamed(timestamp=1_700_000_001)
    monkeypatch.setattr(engine, "sleep", lambda _: source.write_bytes(changed))
    assert preserve_last_replay(tmp_path, source=source) is None
    assert set(source.parent.iterdir()) == {source, previous}
    monkeypatch.setattr(engine, "sleep", lambda _: None)
    destination = preserve_last_replay(tmp_path, source=source)
    assert destination is not None
    assert destination.read_bytes() == source.read_bytes() == changed
    assert preserve_last_replay(tmp_path, source=source) == destination
    destination.write_bytes(b"keep distinct")
    collision = preserve_last_replay(tmp_path, source=source)
    assert collision is not None and collision != destination
    assert collision.read_bytes() == changed and destination.read_bytes() == b"keep distinct"
    source.write_bytes(changed[:72])
    assert preserve_last_replay(tmp_path, source=source) is None
    assert source.read_bytes() == changed[:72]
    assert not list(source.parent.glob(".replay-*"))
    with pytest.raises(ValueError, match="rolling replay"):
        preserve_last_replay(tmp_path, source=collision)


def test_raw_legacy_waits_until_game_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(tmp_path, "_LastReplay.yrp", legacy())
    monkeypatch.setattr(engine, "sleep", lambda _: None)
    monkeypatch.setattr(music_library, "_game_running", lambda _: True)
    assert preserve_last_replay(tmp_path, source=source) is None
    monkeypatch.setattr(music_library, "_game_running", lambda _: False)
    destination = preserve_last_replay(tmp_path)
    assert destination is not None and destination.read_bytes() == source.read_bytes()


def test_export_zip_contains_exact_replay_and_notes(tmp_path: Path) -> None:
    data = streamed()
    source = put(tmp_path, "private original filename.yrpX", data)
    store = AnnotationStore(tmp_path / "settings")
    (original,) = index_replays(tmp_path)
    assert original.replay_id is not None
    store.set(original.replay_id, ReplayNotes("A label", "My notes", ("a tag",)))
    (entry,) = index_replays(tmp_path, annotations=store)
    destination = tmp_path / "share.zip"
    assert export_replay(entry, destination) == destination
    with ZipFile(destination) as archive:
        assert archive.namelist() == ["replay.yrpX", "notes.json"]
        assert archive.read("replay.yrpX") == data
        assert json.loads(archive.read("notes.json")) == {
            "label": "A label",
            "notes": "My notes",
            "tags": ["a tag"],
        }
    with pytest.raises(FileExistsError):
        export_replay(entry, destination)
    source.write_bytes(streamed(turns=5))
    with pytest.raises(ValueError, match="changed"):
        export_replay(entry, tmp_path / "stale.zip")
    assert not (tmp_path / "stale.zip").exists()
    assert not list(tmp_path.glob(".replay-*"))


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_linkless_preserve_and_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    import ctypes
    import errno
    import os
    from types import SimpleNamespace
    from unittest.mock import Mock

    from backup import core

    def exclusive(source: bytes, target: bytes) -> int:
        src, dst = Path(os.fsdecode(source)), Path(os.fsdecode(target))
        assert src.parent == dst.parent and src.stat().st_size
        if dst.exists():
            ctypes.set_errno(errno.EEXIST)
            return -1
        src.rename(dst)
        return 0

    libc = SimpleNamespace(
        renameat2=lambda a, src, b, dst, flag: exclusive(src, dst),
        renamex_np=lambda src, dst, flag: exclusive(src, dst),
    )
    posix = Mock(wraps=os)
    posix.name = "posix"
    # ENOTSUP and EOPNOTSUPP are aliases on Linux and macOS, unlike on the Windows test host.
    posix.link.side_effect = OSError(errno.ENOTSUP, "no hard links")
    monkeypatch.setattr(core, "os", posix)
    monkeypatch.setattr(core, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: libc)
    monkeypatch.setattr(engine, "sleep", lambda _: None)
    data = streamed()
    put(tmp_path, "_LastReplay.yrpX", data)
    saved = preserve_last_replay(tmp_path)
    assert saved and saved.read_bytes() == data
    assert preserve_last_replay(tmp_path) == saved
    entry = index_replays(tmp_path)[0]
    archive = export_replay(entry, tmp_path / "copy.zip")
    with ZipFile(archive) as bundle:
        assert bundle.read("replay.yrpX") == data
    archive.write_bytes(b"keep this")
    with pytest.raises(FileExistsError):
        export_replay(entry, archive)
    assert archive.read_bytes() == b"keep this" and posix.link.call_count == 4

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import music_library as music
from music import catalogue
from music.catalogue import atomic_json

from .test_engine import wav


@pytest.mark.parametrize("damage", ["missing", "changed", "resuming", "undo"])
def test_restore_keeps_evidence(tmp_path: Path, damage: str) -> None:
    game, lib = tmp_path / "game", tmp_path / "lib"
    loose = wav(game / "sound/BGM/Stock.wav", 1)
    wav(loose.parent / "duel/Stock.wav", 2)
    track = music.Track("tf3", "First", wav(lib / "First.wav", 3), "duel")
    music.use_track(game, track, root=lib)
    music.add_to_playlist(
        game, music.Track("tf4", "Second", wav(lib / "Two.wav", 4), "duel"), root=lib
    )
    state = game / "sound/.edopro-hd-sync/duel"
    selected = (state / "selection.json").read_bytes()
    parked = state / "saved/BGM/Stock.wav"
    if damage == "missing":
        parked.unlink()
    elif damage == "changed":
        wav(parked, 9)
    elif damage == "resuming":
        (state / "restoring.json").write_bytes(b"{")
    else:
        next((state / "undo").glob("*.wav")).write_bytes(b"changed undo recording")
    undo = {p: p.read_bytes() for p in (state / "undo").glob("*.wav")}
    with pytest.raises((ValueError, OSError)):
        music.restore_playlist(game, "duel")
    assert (state / "selection.json").read_bytes() == selected
    assert all(p.read_bytes() == data for p, data in undo.items())
    assert (state / "saved/duel/Stock.wav").exists()
    if damage == "resuming":
        assert (state / "restoring.json").read_bytes() == b"{"


def test_journal_sync_failure_preserves_previous(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    atomic_json(path, {"first": 1})
    with patch.object(os, "fsync", side_effect=OSError("sync failed")), pytest.raises(OSError):
        atomic_json(path, {"second": 2})
    assert json.loads(path.read_bytes()) == {"first": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_journal_flushes_bytes_then_replaces_then_syncs_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    path = tmp_path / "pending.json"
    replace = Path.replace

    def sync(fd: int) -> None:
        assert os.fstat(fd).st_size > 0
        events.append("file")

    def promote(source: Path, target: Path) -> Path:
        assert target == path and json.loads(source.read_bytes()) == {"ready": True}
        events.append("replace")
        return replace(source, target)

    def sync_parent(directory: Path) -> None:
        assert directory == path.parent and path.is_file()
        events.append("directory")

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(Path, "replace", promote)
    monkeypatch.setattr(catalogue, "sync_directory", sync_parent)
    atomic_json(path, {"ready": True})
    assert events == ["file", "replace", "directory"]


@pytest.mark.parametrize("boundary", ["publish", "restore", "cleanup"])
def test_retry_after_process_exit(tmp_path: Path, boundary: str) -> None:
    game, lib = tmp_path / "game", tmp_path / "lib"
    first = wav(game / "sound/BGM/duel/Stock.wav", 1)
    loose = wav(game / "sound/BGM/Stock.wav", 2)
    before = {p: p.read_bytes() for p in (first, loose)}
    source = wav(lib / "song.wav", 3)
    if boundary != "publish":
        music.use_track(game, music.Track("tf3", "Song", source, "duel"), root=lib)
    script = """
import os, sys
from pathlib import Path
import music_library as music
root = Path(sys.argv[1])
game, lib = root / "game", root / "lib"
phase = sys.argv[2]
move, write = music._move, music.write_journal
def interrupt_move(source, destination, changes):
    move(source, destination, changes)
    if phase == "publish" or (phase == "restore" and "saved" in source.parts):
        os._exit(23)
def interrupt_cleanup(path, data):
    write(path, data)
    if phase == "cleanup" and path.name == "restoring.json" and data.get("verified"):
        os._exit(23)
music._move, music.write_journal = interrupt_move, interrupt_cleanup
if phase == "publish":
    music.use_track(game, music.Track("tf3", "Song", lib / "song.wav", "duel"), root=lib)
else:
    music.restore_playlist(game, "duel")
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), boundary], timeout=20)
    assert result.returncode == 23
    assert music.can_restore(game, "duel")
    music.restore_playlist(game, "duel")
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not music.can_restore(game, "duel")
    assert list(first.parent.glob("*.wav")) == [first]
    if boundary != "publish":
        music.undo_playlist(game, "duel", root=lib)
        assert music.playlist(game, "duel", root=lib)[0].digest == music._digest(source)


def test_reject_valid_json_with_truncated_inventory(tmp_path: Path) -> None:
    game, lib = tmp_path / "game", tmp_path / "lib"
    wav(game / "sound/BGM/duel/Stock.wav", 1)
    music.use_track(game, music.Track("tf3", "Song", wav(lib / "song.wav", 3), "duel"), root=lib)
    state = game / "sound/.edopro-hd-sync/duel"
    selection = state / "selection.json"
    record = json.loads(selection.read_bytes())
    record["originals"].clear()
    selection.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="record has changed"):
        music.restore_playlist(game, "duel")
    assert selection.exists() and (state / "saved/duel/Stock.wav").exists()


def test_split_mount_before_moving_tracks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game, lib = tmp_path / "game", tmp_path / "lib"
    original = wav(game / "sound/BGM/duel/Stock.wav", 4)
    track = music.Track("tf3", "First", wav(lib / "First.wav", 3), "duel")
    stat = Path.stat

    def split(path: Path, **kwargs: bool) -> os.stat_result:
        info = stat(path, **kwargs)
        if original.parent in (path, *path.parents):
            fields = list(info)
            fields[2] = info.st_dev + 1
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(Path, "stat", split)
    with (
        patch.object(music, "_move", side_effect=AssertionError("attempted track move")),
        pytest.raises(OSError, match="filesystem"),
    ):
        music.use_track(game, track, root=lib)
    assert original.is_file()
    assert not music.can_restore(game, "duel")

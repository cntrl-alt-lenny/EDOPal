import ctypes
import errno
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import config
import durable_write as durable
import tournament_decks as decks
from backup import core, journal


def test_journal_flushes_before_changing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal._record(tmp_path, "pending", [])
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("flush failed")))
    with pytest.raises(OSError, match="flush failed"):
        journal._record(tmp_path, "complete", [])
    assert [p.name for p in tmp_path.iterdir()] == ["journal.json"]
    assert json.loads((tmp_path / "journal.json").read_bytes())["status"] == "pending"


def test_file_flush_precedes_replace_then_directory_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "file"
    destination.write_bytes(b"old")
    events = []
    real_fsync = os.fsync

    def sync_file(fd: int) -> None:
        assert os.fstat(fd).st_size == 3 and destination.read_bytes() == b"old"
        real_fsync(fd)
        events.append("file")

    def replace(stage: Path, final: Path) -> None:
        assert stage.parent == final.parent and stage.read_bytes() == b"new"
        stage.replace(final)
        events.append("replace")

    def sync_parent(parent: Path) -> None:
        assert parent == tmp_path and destination.read_bytes() == b"new"
        events.append("directory")

    monkeypatch.setattr(os, "fsync", sync_file)
    monkeypatch.setattr(durable, "sync_directory", sync_parent)
    durable.durable_write(destination, b"new", publish=replace)
    assert events == ["file", "replace", "directory"]
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_deck_on_volume_without_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    # Route just the publication branch through a simulated FAT mount. The native
    # rename stand-in observes the complete staged file at the publication boundary.
    unsupported = Mock(side_effect=OSError(errno.EPERM, "no hard links"))
    posix = SimpleNamespace(name="posix", link=unsupported, fsencode=os.fsencode)
    monkeypatch.setattr(decks, "os", posix, raising=False)
    monkeypatch.setattr(core, "os", posix)
    monkeypatch.setattr(core, "sys", SimpleNamespace(platform=platform))
    content = b"#main\n12345\n"
    seen = []

    def rename(source: bytes, target: bytes) -> int:
        stage, final = Path(os.fsdecode(source)), Path(os.fsdecode(target))
        assert stage.parent == final.parent and stage.read_bytes() == content
        assert not final.exists()
        seen.append(final)
        stage.rename(final)
        return 0

    libc = SimpleNamespace(
        renameat2=lambda _a, src, _b, dst, _flag: rename(src, dst),
        renamex_np=lambda src, dst, _flag: rename(src, dst),
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: libc)
    original_open = Path.open

    def observe(path: Path, mode: str = "r", *args, **kwargs):
        assert not (path.suffix == ".ydk" and "x" in mode), "partial final deck is visible"
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe)
    saved, added = decks.publish_ydk(tmp_path, "Event", content)
    assert added and seen == [saved] and saved.read_bytes() == content


def test_deck_keeps_open_case_variant_and_original_spelling(tmp_path: Path) -> None:
    folder = tmp_path / "deck"
    folder.mkdir()
    original = folder / "EVENT.YDK"
    original.write_bytes(b"mine")
    with original.open("rb") as held:
        same, added = decks.publish_ydk(tmp_path, "Event", b"mine", reuse_identical=True)
        assert not added and same.name == "EVENT.YDK"
        copy, added = decks.publish_ydk(tmp_path, "Event", b"different", reuse_identical=True)
        assert added and copy.name == "Event (2).ydk"
        assert held.read() == b"mine"
    assert copy.read_bytes() == b"different"


def test_refused_deck_rename_removes_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publish = core.publish_new

    def refused(stage: Path, target: Path) -> None:
        assert stage.read_bytes() == b"new" and not target.exists()
        if os.name == "nt":
            # An ordinary Windows file handle denies delete/rename sharing.
            with stage.open("rb"):
                publish(stage, target)
        else:
            raise PermissionError(errno.EACCES, "rename denied")

    monkeypatch.setattr(core, "publish_new", refused)
    with pytest.raises(PermissionError):
        decks.publish_ydk(tmp_path, "Test", b"new")
    assert not list((tmp_path / "deck").iterdir())


def test_failed_deck_flush_leaves_only_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original, _ = decks.publish_ydk(tmp_path, "Deck", b"original")
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("card removed")))
    with pytest.raises(OSError, match="card removed"):
        decks.publish_ydk(tmp_path, "Deck", b"new")
    assert list(original.parent.iterdir()) == [original] and original.read_bytes() == b"original"


def test_two_process_preference_saves(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"legacy": 7}', encoding="utf-8")
    # Hold the first writer immediately after its JSON read. The second writer
    # either finishes during the pause (old race) or waits for the lock.
    worker = """
import json, sys, time
from pathlib import Path
import config
path, key = sys.argv[1:]
if key == 'first':
    read = json.load
    def paused(stream):
        result = read(stream)
        stream.close()
        print('read', flush=True)
        time.sleep(1)
        return result
    json.load = paused
sys.exit(0 if config.save_setting(path, key, True) else 1)
"""
    with subprocess.Popen(
        [sys.executable, "-c", worker, str(path), "first"],
        stdout=subprocess.PIPE,
        text=True,
    ) as first:
        assert first.stdout and first.stdout.readline().strip() == "read"
        subprocess.run([sys.executable, "-c", worker, str(path), "second"], check=True, timeout=15)
        assert first.wait(timeout=15) == 0
    assert json.loads(path.read_bytes()) == {"legacy": 7, "first": True, "second": True}


@pytest.mark.parametrize("broken", [b'{"field_art":', b"\xff\xfe", b"[]"])
def test_damaged_preferences_preserved(tmp_path: Path, broken: bytes) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(broken)
    cfg = config.Config(["--config", str(path)], defer_discovery=True)
    assert cfg.field_art == config.DEFAULTS["field_art"]
    assert config.save_setting(str(path), "original_text", True)
    assert json.loads(path.read_bytes()) == {"original_text": True}
    assert [p.read_bytes() for p in tmp_path.glob("config.json.corrupt-*")] == [broken]


def test_stale_era_snapshots_merge_edits_and_deletions(tmp_path: Path) -> None:
    path = str(tmp_path / "config.json")
    assert config.save_setting(path, "era_art_applied", {"A": {"1": "old", "2": "old"}})
    a, b = (config.Config(["--config", path], defer_discovery=True) for _ in range(2))
    a.era_art_applied["B"] = {"1": "new"}
    a.era_art_applied["A"]["1"] = "new"
    assert config.save_setting(path, "era_art_applied", a.era_art_applied)
    del b.era_art_applied["A"]["2"]
    assert config.save_setting(path, "era_art_applied", b.era_art_applied)
    assert config._load_config_file(path)["era_art_applied"] == {
        "A": {"1": "new"},
        "B": {"1": "new"},
    }


def test_failed_preference_flush_preserves_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    old = b'{"unknown": true}'
    path.write_bytes(old)
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("storage error")))
    assert not config.save_setting(str(path), "force", True)
    assert path.read_bytes() == old
    assert not list(tmp_path.glob("*.tmp"))

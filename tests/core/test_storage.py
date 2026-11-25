import codecs
import os
import stat
from pathlib import Path

import pytest

from edopal.core.install import Install, RunState
from edopal.core.paths import Paths
from edopal.core.safe_storage import SafeStorage
from edopal.core.storage import GameRunningError, UndoResult


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "config", tmp_path / "data", tmp_path / "cache")


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "Project Ignis"
    root.mkdir()
    return Install(root)


@pytest.fixture
def storage(paths):
    return SafeStorage(paths, lambda: RunState.CLOSED)


def link(path: Path, target: Path, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"This filesystem cannot make a symlink: {exc}")


def test_round_trip_after_restart(paths, install, storage, tmp_path):
    original = install.path("file to replace")
    original.write_bytes(b"original")
    removed = install.path("empty file")
    removed.touch()
    staged = tmp_path / "asset"
    staged.write_bytes(b"cached pack data")
    copied = install.path("skin", "copy")
    with storage.changeset(install, "themes", "Apply theme") as cs:
        cs.write("file to replace", b"first edit")
        cs.write("file to replace", b"last edit")
        cs.copy_in(staged, "skin/copy")
        cs.remove("empty file")
        assert original.read_bytes() == b"original"
        assert removed.is_file()
        assert not copied.exists()

    assert original.read_bytes() == b"last edit"
    assert not removed.exists()
    assert copied.read_bytes() == b"cached pack data"
    assert (paths.data / "journal").is_dir()
    receipt = storage.last_receipt(install, "themes")
    assert receipt.feature == "themes"
    assert receipt.title == "Apply theme"
    assert sorted(receipt.changed) == ["empty file", "file to replace", "skin/copy"]

    restarted = SafeStorage(paths.data, lambda: RunState.CLOSED)
    assert list(restarted.history(install)) == [receipt]
    result = restarted.undo_last(install, "themes")
    assert sorted(result.restored) == sorted(receipt.changed)
    assert not result.conflicts
    assert original.read_bytes() == b"original"
    assert removed.read_bytes() == b""
    assert not copied.exists()
    assert restarted.undo_last(install, "themes") == UndoResult(())


def test_an_aborted_body_and_no_op_runs_preserve_previous_receipt(install, storage):
    with storage.changeset(install, "music", "First mix") as cs:
        cs.write("menu.ogg", b"intro")
    first = storage.last_receipt(install, "music")

    with pytest.raises(RuntimeError, match="cancelled"), storage.changeset(install, "music", "Cancelled") as cs:
        cs.write("menu.ogg", b"new intro")
        cs.write("deck.ogg", b"new song")
        raise RuntimeError("cancelled")
    with storage.changeset(install, "music", "Already current") as cs:
        cs.write("menu.ogg", b"intro")
        cs.remove("unassigned.ogg")
    assert storage.last_receipt(install, "music") == first
    assert install.path("menu.ogg").read_bytes() == b"intro"
    assert not install.path("deck.ogg").exists()


@pytest.mark.parametrize(
    ("state", "confirmed", "allowed"),
    [
        (RunState.CLOSED, False, True),
        (RunState.UNKNOWN, False, False),
        (RunState.UNKNOWN, True, True),
        (RunState.RUNNING, False, False),
        (RunState.RUNNING, True, False),
    ],
)
def test_checks_game_state_at_commit(paths, install, state, confirmed, allowed):
    current = RunState.CLOSED
    storage = SafeStorage(paths, lambda: current)

    def submit():
        nonlocal current
        with storage.changeset(install, "formats", "Install banlist", confirmed_closed=confirmed) as cs:
            cs.write("list.conf", b"limits")
            current = state

    if allowed:
        submit()
        assert install.path("list.conf").read_bytes() == b"limits"
    else:
        with pytest.raises(GameRunningError):
            submit()
        assert not install.path("list.conf").exists()
        assert storage.last_receipt(install, "formats") is None


def test_undo_checks_game_state(paths, install):
    state = RunState.CLOSED
    store = SafeStorage(paths, lambda: state)
    with store.changeset(install, "formats", "Install deck") as cs:
        cs.write("deck/test.ydk", b"deck")

    state = RunState.RUNNING
    with pytest.raises(GameRunningError):
        store.undo_last(install, "formats", confirmed_closed=True)
    state = RunState.UNKNOWN
    with pytest.raises(GameRunningError):
        store.undo_last(install, "formats")
    assert install.path("deck", "test.ydk").read_bytes() == b"deck"
    assert store.undo_last(install, "formats", confirmed_closed=True).restored == ("deck/test.ydk",)


def test_run_state_uses_captured_install_when_user_switches_games(paths, install, tmp_path):
    other = Install(tmp_path / "other-game")
    other.root.mkdir()
    selected = install
    running = None

    def run_state(install=None):
        return RunState.RUNNING if (install or selected) == running else RunState.CLOSED

    store = SafeStorage(paths, run_state)
    with store.changeset(install, "themes", "Initial") as cs:
        cs.write("skin.xml", b"old")
    with pytest.raises(GameRunningError), store.changeset(install, "themes", "Queued for first game") as cs:
        cs.write("skin.xml", b"new")
        selected, running = other, install
    with pytest.raises(GameRunningError):
        store.undo_last(install, "themes")
    assert install.path("skin.xml").read_bytes() == b"old"
    assert not other.path("skin.xml").exists()


def test_undo_of_removed_file_recreates_its_missing_parent(install, storage):
    original = install.path("deck", "custom", "old.ydk")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"deck")
    with storage.changeset(install, "formats", "Remove deck") as cs:
        cs.remove("deck/custom/old.ydk")
    original.parent.rmdir()
    original.parent.parent.rmdir()
    assert storage.undo_last(install, "formats") == UndoResult(("deck/custom/old.ydk",))
    assert original.read_bytes() == b"deck"


def test_undo_keeps_later_edits_and_retries_only_conflicts(install, storage):
    config = install.path("system.conf")
    config.write_bytes(b"skin = Stock\n")
    track = install.path("removed.ogg")
    track.write_bytes(b"previous mix")
    with storage.changeset(install, "themes", "Choose theme") as cs:
        cs.set_conf("system.conf", "skin", "Abyss")
        cs.remove("removed.ogg")
        cs.write("missing.png", b"new art")
        cs.write("cover.png", b"new cover")

    published = config.read_bytes()
    config.write_bytes(published + b"volume = 15\n")
    track.write_bytes(b"custom mix")
    install.path("missing.png").unlink()
    result = storage.undo_last(install, "themes")
    assert result.restored == ("cover.png",)
    assert sorted(result.conflicts) == ["missing.png", "removed.ogg", "system.conf"]
    assert config.read_bytes() == published + b"volume = 15\n"
    assert track.read_bytes() == b"custom mix"
    assert not install.path("missing.png").exists()

    config.write_bytes(published)
    track.unlink()
    install.path("missing.png").write_bytes(b"new art")
    retry = storage.undo_last(install, "themes")
    assert sorted(retry.restored) == sorted(result.conflicts)
    assert not retry.conflicts
    assert config.read_bytes() == b"skin = Stock\n"
    assert track.read_bytes() == b"previous mix"
    assert not install.path("missing.png").exists()
    assert not install.path("cover.png").exists()


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [
        ("utf-8", b""),
        ("utf-8", codecs.BOM_UTF8),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
        ("cp1252", b""),
    ],
)
def test_conf_keeps_formatting_comments_and_encoding(install, storage, encoding, bom):
    original = "# réglages\r\n# skin = ignored\r\nskin = Shadowed\r\n  skin\t=  Stock  # active\r\nenable_music = 0\r\n"
    config = install.path("system.conf")
    config.write_bytes(bom + original.encode(encoding))
    with storage.changeset(install, "themes", "Apply") as cs:
        cs.set_conf("system.conf", "skin", "Abyss")
        cs.set_conf("system.conf", "enable_music", "1")
        cs.set_conf("system.conf", "textfont", "fonts/new.ttf 12")
    expected = original.replace("Stock", "Abyss").replace("enable_music = 0", "enable_music = 1")
    expected += "textfont = fonts/new.ttf 12\r\n"
    assert config.read_bytes() == bom + expected.encode(encoding)
    assert storage.undo_last(install, "themes").restored == ("system.conf",)
    assert config.read_bytes() == bom + original.encode(encoding)


def test_conf_reads_current_file_under_lock_and_composes_queued_operations(install, storage):
    config = install.path("system.conf")
    config.write_bytes(b"skin = Stock\n")
    with storage.changeset(install, "themes", "Change skin") as cs:
        cs.set_conf("system.conf", "skin", "Abyss")
        config.write_bytes(b"skin = Stock\nmusic_volume = 25\n")
        cs.write("second.conf", b"# new file\nskin = Stock")
        cs.set_conf("second.conf", "skin", "Moon")
    assert config.read_bytes() == b"skin = Abyss\nmusic_volume = 25\n"
    assert install.path("second.conf").read_bytes() == b"# new file\nskin = Moon"
    storage.undo_last(install, "themes")
    assert config.read_bytes() == b"skin = Stock\nmusic_volume = 25\n"
    assert not install.path("second.conf").exists()


def test_latest_twenty_per_feature_per_install(tmp_path, install, storage):
    other = Install(tmp_path / "other")
    other.root.mkdir()
    for game, feature in ((install, "themes"), (other, "hdcards")):
        with storage.changeset(game, feature, "Keep this receipt") as cs:
            cs.write("same-name", b"independent")
    for n in range(22):
        with storage.changeset(install, "hdcards", f"Sync {n}") as cs:
            cs.write("art", str(n).encode())
    titles = [r.title for r in storage.history(install) if r.feature == "hdcards"]
    assert len(titles) == 20
    assert set(titles) == {f"Sync {n}" for n in range(2, 22)}
    for n in reversed(range(2, 22)):
        assert storage.last_receipt(install, "hdcards").title == f"Sync {n}"
        assert storage.undo_last(install, "hdcards").restored == ("art",)
        assert install.path("art").read_bytes() == str(n - 1).encode()
    assert [r.title for r in storage.history(install)] == ["Keep this receipt"]
    assert [r.title for r in storage.history(other)] == ["Keep this receipt"]
    assert (other.path("same-name")).read_bytes() == b"independent"


@pytest.mark.parametrize("bad", ["../outside", "pics/../valid", "/absolute", ""])
def test_invalid_path_aborts_the_whole_changeset(storage, install, bad):
    with pytest.raises((ValueError, OSError)), storage.changeset(install, "hdcards", "Sync") as cs:
        cs.write("first.jpg", b"first")
        cs.write(bad, b"bad")
    assert not install.path("first.jpg").exists()
    assert storage.last_receipt(install, "hdcards") is None


@pytest.mark.parametrize("directory", [True, False])
def test_changeset_refuses_symlink_escape(tmp_path, install, storage, directory):
    other_folder = tmp_path / "elsewhere"
    other_folder.mkdir()
    outside = other_folder / "asset"
    outside.write_bytes(b"original")
    rel = "linked/asset" if directory else "linked"
    link(install.path("linked"), other_folder if directory else outside, directory=directory)
    with pytest.raises((ValueError, OSError)), storage.changeset(install, "themes", "Install") as cs:
        cs.write(rel, b"new")
    assert outside.read_bytes() == b"original"
    assert storage.last_receipt(install, "themes") is None


def test_canonical_root_alias_uses_same_receipts(tmp_path, install, storage):
    alias = Install(tmp_path / "shortcut")
    link(alias.root, install.root, directory=True)
    with storage.changeset(alias, "music", "Add") as cs:
        cs.write("menu.ogg", b"music")
    assert storage.last_receipt(alias, "music") == storage.last_receipt(install, "music")
    assert storage.undo_last(install, "music").restored == ("menu.ogg",)
    assert storage.last_receipt(alias, "music") is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_preserves_permissions_on_replace_and_restore(install, storage):
    dest = install.path("system.conf")
    dest.write_bytes(b"old")
    dest.chmod(0o640)
    with storage.changeset(install, "themes", "Replace") as cs:
        cs.write("system.conf", b"new")
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    storage.undo_last(install, "themes")
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="Directory fsync needs POSIX file descriptors")
def test_publication_flushes_staged_file_then_destination_directory(install, storage, monkeypatch):
    parent = install.path("pics")
    parent.mkdir()
    dest = parent / "face.jpg"
    dest.write_bytes(b"old")
    fsync, replace = os.fsync, os.replace
    events = []

    def record_sync(fd):
        fsync(fd)
        info = os.fstat(fd)
        events.append(("sync", info.st_dev, info.st_ino))

    def record_replace(src, dst, **kwargs):
        if Path(dst).name == dest.name:
            stage = os.stat(src, dir_fd=kwargs.get("src_dir_fd"))
            assert stage.st_dev == parent.stat().st_dev
            assert ("sync", stage.st_dev, stage.st_ino) in events
            events.append(("publish",))
        replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "fsync", record_sync)
    monkeypatch.setattr(os, "replace", record_replace)
    with storage.changeset(install, "hdcards", "Sync") as cs:
        cs.write("pics/face.jpg", b"new")
    directory = parent.stat()
    assert ("sync", directory.st_dev, directory.st_ino) in events[events.index(("publish",)) + 1 :]
    assert dest.read_bytes() == b"new"


def test_artwork_batch_uses_one_undo_point(install, storage):
    names = [f"pics/{n}.jpg" for n in range(128)]
    payload = bytes(48 * 1024)
    original = install.path(names[0])
    original.parent.mkdir()
    original.write_bytes(b"old")
    with storage.changeset(install, "hdcards", "Card sync") as cs:
        for name in names:
            cs.write(name, payload + name.encode())
    assert all(install.path(name).read_bytes() == payload + name.encode() for name in names)
    assert len(list(storage.history(install))) == 1
    assert set(storage.undo_last(install, "hdcards").restored) == set(names)
    assert list(original.parent.iterdir()) == [original]
    assert original.read_bytes() == b"old"

"""Exercise process death and install locking through the public changeset API."""

import multiprocessing
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

from edopal.core.install import Install, RunState
from edopal.core.paths import Paths
from edopal.core.safe_storage import SafeStorage
from edopal.core.storage import GameRunningError


def _crash_after_one_file(paths, install, undo):
    replace = os.replace

    def publish_then_exit(source, dest, **kwargs):
        replace(source, dest, **kwargs)
        if Path(dest).name in {"one.jpg", "two.jpg"}:
            os._exit(74)  # No finally blocks, context-manager exits or SQLite connection close.

    os.replace = publish_then_exit
    store = SafeStorage(paths, lambda: RunState.CLOSED)
    if undo:
        store.undo_last(install, "hdcards")
    else:
        with store.changeset(install, "hdcards", "Interrupted") as cs:
            cs.write("one.jpg", b"new")
            cs.write("two.jpg", b"new")


def _crash_while_staging(paths, install):
    fsync = os.fsync
    payload = b"one staged asset" * 111

    def stop_on_staged_file(fd):
        fsync(fd)
        if os.fstat(fd).st_size == len(payload):
            os._exit(75)

    os.fsync = stop_on_staged_file
    store = SafeStorage(paths, lambda: RunState.CLOSED)
    with store.changeset(install, "hdcards", "Interrupted staging") as cs:
        cs.write("one.jpg", payload)
        cs.write("two.jpg", payload)


def _wait_for_process(process, *, code=0):
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("Storage worker did not complete")
    assert process.exitcode == code


@pytest.fixture
def game(tmp_path):
    paths = Paths(tmp_path / "config", tmp_path / "data", tmp_path / "cache")
    install = Install(tmp_path / "game")
    install.root.mkdir()
    for name in ("one.jpg", "two.jpg"):
        install.path(name).write_bytes(b"old")
    return paths, install


def test_next_use_cleans_up_interrupted_staging(game):
    paths, install = game
    store = SafeStorage(paths, lambda: RunState.CLOSED)
    with store.changeset(install, "hdcards", "Earlier run") as cs:
        cs.write("earlier.jpg", b"saved")
    before = set(install.root.rglob("*"))
    process = multiprocessing.get_context("spawn").Process(target=_crash_while_staging, args=(paths, install))
    process.start()
    _wait_for_process(process, code=75)
    assert install.path("one.jpg").read_bytes() == b"old"
    assert install.path("two.jpg").read_bytes() == b"old"
    restarted = SafeStorage(paths, lambda: RunState.CLOSED)
    assert [r.title for r in restarted.history(install)] == ["Earlier run"]
    assert set(install.root.rglob("*")) == before


@pytest.mark.parametrize("external_edit", [False, True])
def test_next_use_rolls_back_interrupted_commit(game, external_edit):
    paths, install = game
    closed = SafeStorage(paths, lambda: RunState.CLOSED)
    with closed.changeset(install, "hdcards", "Last completed sync") as cs:
        cs.write("earlier.jpg", b"earlier")
    process = multiprocessing.get_context("spawn").Process(target=_crash_after_one_file, args=(paths, install, False))
    process.start()
    _wait_for_process(process, code=74)

    files = (install.path("one.jpg"), install.path("two.jpg"))
    assert sorted(file.read_bytes() for file in files) == [b"new", b"old"]
    changed = next(file for file in files if file.read_bytes() == b"new")
    if external_edit:
        changed.write_bytes(b"user art")

    running = SafeStorage(paths, lambda: RunState.RUNNING)
    with pytest.raises(GameRunningError), running.changeset(install, "music", "New mix") as cs:
        cs.write("deck.ogg", b"song")
    assert changed.read_bytes() == (b"user art" if external_edit else b"new")
    assert not install.path("deck.ogg").exists()

    restarted = SafeStorage(paths, lambda: RunState.CLOSED)
    assert [r.title for r in restarted.history(install)] == ["Last completed sync"]
    assert all(file.read_bytes() == (b"user art" if external_edit and file == changed else b"old") for file in files)
    assert install.path("earlier.jpg").read_bytes() == b"earlier"
    assert restarted.undo_last(install, "hdcards").restored == ("earlier.jpg",)


@pytest.mark.parametrize("retry_undo", [False, True])
def test_next_use_finishes_interrupted_undo(game, retry_undo):
    paths, install = game
    store = SafeStorage(paths, lambda: RunState.CLOSED)
    with store.changeset(install, "hdcards", "Earlier run") as cs:
        cs.write("first.jpg", b"keep")
    with store.changeset(install, "hdcards", "Change both") as cs:
        cs.write("one.jpg", b"new")
        cs.write("two.jpg", b"new")

    process = multiprocessing.get_context("spawn").Process(target=_crash_after_one_file, args=(paths, install, True))
    process.start()
    _wait_for_process(process, code=74)
    files = (install.path("one.jpg"), install.path("two.jpg"))
    assert sorted(file.read_bytes() for file in files) == [b"new", b"old"]

    restarted = SafeStorage(paths, lambda: RunState.CLOSED)
    if retry_undo:
        restarted.undo_last(install, "hdcards")
    assert restarted.last_receipt(install, "hdcards").title == "Earlier run"
    assert [file.read_bytes() for file in files] == [b"old", b"old"]
    assert install.path("first.jpg").read_bytes() == b"keep"


def test_missing_copy_source_after_staging_preserves_files_and_history(game):
    paths, install = game
    storage = SafeStorage(paths, lambda: RunState.CLOSED)
    with storage.changeset(install, "hdcards", "First run") as cs:
        cs.write("one.jpg", b"saved")
    before = set(install.root.iterdir())
    with pytest.raises(FileNotFoundError), storage.changeset(install, "hdcards", "Second run") as cs:
        cs.write("one.jpg", b"updated")
        cs.copy_in(paths.cache / "unavailable-asset", "two.jpg")
    assert set(install.root.iterdir()) == before
    assert install.path("one.jpg").read_bytes() == b"saved"
    assert install.path("two.jpg").read_bytes() == b"old"
    assert [r.title for r in storage.history(install)] == ["First run"]


def test_copy_rejects_non_regular_source_without_publishing_prior_edits(game):
    paths, install = game
    storage = SafeStorage(paths, lambda: RunState.CLOSED)
    with pytest.raises((ValueError, OSError)), storage.changeset(install, "hdcards", "Copy assets") as cs:
        cs.write("one.jpg", b"new")
        cs.copy_in(Path(os.devnull), "two.jpg")
    assert install.path("one.jpg").read_bytes() == b"old"
    assert install.path("two.jpg").read_bytes() == b"old"
    assert not list(storage.history(install))
    assert not list(install.root.glob(".edopal-stage-*"))


def test_damaged_later_original_leaves_files_and_receipt_untouched(game):
    paths, install = game
    storage = SafeStorage(paths, lambda: RunState.CLOSED)
    with storage.changeset(install, "hdcards", "Sync both") as cs:
        cs.write("one.jpg", b"new one")
        cs.write("two.jpg", b"new two")
    receipt = storage.last_receipt(install, "hdcards")
    (journal,) = (paths.data / "journal").glob("*/history.sqlite")
    with closing(sqlite3.connect(journal)) as db, db:
        assert db.execute("UPDATE files SET original=? WHERE rel='two.jpg'", (b"damaged backup",)).rowcount == 1
    with pytest.raises(OSError, match="two.jpg"):
        storage.undo_last(install, "hdcards")
    assert install.path("one.jpg").read_bytes() == b"new one"
    assert install.path("two.jpg").read_bytes() == b"new two"
    with closing(sqlite3.connect(journal)) as db:
        assert db.execute("SELECT state FROM receipts WHERE id=?", (receipt.id,)).fetchone() == ("ready",)
    assert list(storage.history(install)) == [receipt]


def test_changed_second_stage_rolls_back_first_publication(game, monkeypatch):
    paths, install = game
    storage = SafeStorage(paths, lambda: RunState.CLOSED)
    with storage.changeset(install, "hdcards", "Earlier run") as cs:
        cs.write("earlier.jpg", b"saved")
    receipt = storage.last_receipt(install, "hdcards")
    replace = os.replace

    def inject_damage(src, dest, **kwargs):
        replace(src, dest, **kwargs)
        if Path(dest).name == "one.jpg" and install.path("one.jpg").read_bytes() == b"new one":
            (remaining_stage,) = install.root.glob(".edopal-stage-*")
            assert remaining_stage.read_bytes() == b"new two"
            remaining_stage.write_bytes(b"damaged stage")

    monkeypatch.setattr(os, "replace", inject_damage)
    with pytest.raises(OSError, match="two.jpg"), storage.changeset(install, "hdcards", "Sync both") as cs:
        cs.write("one.jpg", b"new one")
        cs.write("two.jpg", b"new two")
    assert install.path("one.jpg").read_bytes() == b"old"
    assert install.path("two.jpg").read_bytes() == b"old"
    assert not list(install.root.glob(".edopal-stage-*"))
    assert list(storage.history(install)) == [receipt]


@pytest.mark.skipif(os.name != "nt", reason="Windows rejects replacement and deletion of read-only files")
def test_failed_windows_replace_cleans_readonly_stage_and_preserves_destination(game):
    paths, install = game
    storage = SafeStorage(paths, lambda: RunState.CLOSED)
    readonly = install.path("two.jpg")
    readonly.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError), storage.changeset(install, "hdcards", "Sync both") as cs:
            cs.write("one.jpg", b"new one")
            cs.write("two.jpg", b"new two")
        assert install.path("one.jpg").read_bytes() == b"old"
        assert readonly.read_bytes() == b"old"
        assert not (readonly.stat().st_mode & stat.S_IWRITE)
        assert not list(install.root.glob(".edopal-stage-*"))
        assert not list(storage.history(install))
    finally:
        readonly.chmod(stat.S_IWRITE)


def _write_in_child(paths, install, content, submitted, checking, resume):
    def check_state():
        checking.set()
        if not resume.wait(10):
            raise TimeoutError("Test did not release the game-state check")
        return RunState.CLOSED

    store = SafeStorage(paths, check_state)
    with store.changeset(install, "themes", content.decode()) as cs:
        cs.write("shared.conf", content)
        submitted.set()


def test_processes_serialize_game_state_checks_and_undo_chain(game, tmp_path):
    paths, install = game
    alias = tmp_path / "game alias"
    try:
        alias.symlink_to(install.root, target_is_directory=True)
    except OSError:
        # Windows users can have the same canonical installation without link privileges.
        alias = install.root
    ctx = multiprocessing.get_context("spawn")
    first_check, second_check, first_submitted, second_submitted, resume = (ctx.Event() for _ in range(5))
    first = ctx.Process(target=_write_in_child, args=(paths, install, b"first", first_submitted, first_check, resume))
    second = ctx.Process(
        target=_write_in_child, args=(paths, Install(alias), b"second", second_submitted, second_check, resume)
    )
    started = []
    try:
        first.start()
        started.append(first)
        assert first_check.wait(10)
        second.start()
        started.append(second)
        assert second_submitted.wait(10)
        assert not second_check.wait(0.2), "Another process entered run_state while the first held the lock"
    finally:
        resume.set()
        for process in started:
            _wait_for_process(process)
    assert second_check.is_set()
    assert install.path("shared.conf").read_bytes() == b"second"
    store = SafeStorage(paths, lambda: RunState.CLOSED)
    assert store.undo_last(install, "themes").restored == ("shared.conf",)
    assert install.path("shared.conf").read_bytes() == b"first"
    assert store.undo_last(install, "themes").restored == ("shared.conf",)
    assert not install.path("shared.conf").exists()

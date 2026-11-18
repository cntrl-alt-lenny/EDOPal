import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import backup
from backup import restore
from music.settings import _linux_game_running


def test_proton_process_blocks_preference_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)
    monkeypatch.setattr(Path, "readlink", lambda _: Path("/usr/bin/wine64-preloader"))
    cmdline = proc / "cmdline"
    cmdline.write_bytes(b"wine64\0C:\\ProjectIgnis\\EDOPro.exe\0")
    assert _linux_game_running(tmp_path, proc.parent)
    cmdline.write_bytes(b"C:\\Other\\SomeGame.exe\0")
    assert not _linux_game_running(tmp_path, proc.parent)


@pytest.mark.parametrize("phase", ["staging", "publication"])
def test_process_exit(pair: tuple, snapshot: Path, phase: str) -> None:
    src, dest = pair
    src.target("game/deck").mkdir()
    for n in ("a", "b"):
        src.target(f"game/deck/{n}.ydk").write_bytes(n.encode())
    backup.create(src, snapshot)
    child = """
import os, sys
from pathlib import Path
import backup
install = backup.Installation(Path(sys.argv[2]), Path(sys.argv[3]))
def stop(done, total, name):
    if done == int(sys.argv[4]):
        os._exit(23)
backup.execute(backup.plan_restore(Path(sys.argv[1]), install), progress=stop)
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(snapshot),
            str(dest.game),
            str(dest.data),
            "1" if phase == "staging" else "3",
        ],
        check=False,
        timeout=20,
    )
    assert process.returncode == 23
    if phase == "staging":
        assert not backup.transactions(dest) and not dest.target("game/deck").exists()
        backup.execute(backup.plan_restore(snapshot, dest))
        assert not list(dest.data.rglob(".staging-*"))
    else:
        (tx,) = backup.transactions(dest)
        assert tx.status == "pending"
        assert dest.target("game/deck/a.ydk").read_bytes() == b"a"
        assert not dest.target("game/deck/b.ydk").exists()
        snapshot.unlink()
        backup.recover(dest, tx.id, "finish")
    assert dest.target("game/deck/b.ydk").read_bytes() == b"b"


def test_destination_reparse_point(pair: tuple, snapshot: Path, tmp_path: Path) -> None:
    src, dest = pair
    src.target("game/deck/sub").mkdir(parents=True)
    src.target("game/deck/sub/list.ydk").write_bytes(b"list")
    backup.create(src, snapshot)
    outside = tmp_path / "outside"
    outside.mkdir()
    dest.target("game/deck").mkdir()
    redirect = dest.target("game/deck/sub")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(redirect))
    else:
        redirect.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="Linked"):
            backup.plan_restore(snapshot, dest)
        assert not list(outside.iterdir())
    finally:
        if os.name == "nt":
            redirect.rmdir()
        else:
            redirect.unlink()


def test_disk_budget_and_journal_integrity(
    pair: tuple, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dest = pair
    src.target("game/deck").mkdir()
    src.target("game/deck/new.ydk").write_bytes(b"new")
    deck = dest.target("game/deck/new.ydk")
    deck.parent.mkdir()
    deck.write_bytes(b"mine")
    manifest = backup.create(src, snapshot)
    plan = backup.plan_restore(snapshot, dest, "take_theirs")
    with monkeypatch.context() as patch:
        patch.setattr(restore.shutil, "disk_usage", lambda _: SimpleNamespace(free=6))
        with pytest.raises(OSError, match="Insufficient free space"):
            backup.execute(plan)
    assert deck.read_bytes() == b"mine" and not backup.transactions(dest)
    tx = backup.execute(plan)
    (blob,) = dest.data.rglob(manifest.entries[0].sha256)
    blob.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="checksum"):
        backup.recover(dest, tx.id, "rollback")
    assert deck.read_bytes() == b"new" and backup.transactions(dest) == (tx,)

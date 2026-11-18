import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import backup
from backup import restore
from state_lock import appearance_lock, locked

from .test_collection import legacy_archive
from .test_transfer import put


def test_second_instance_cannot_lose_a_successful_audio_edit(
    pair: tuple, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dst = pair
    name = "game/config/system.conf"
    put(src, name, b"nickname = incoming\n")
    conf = put(dst, name, b"nickname = local\nenable_music = 0\n")
    backup.create(src, snapshot, ["preferences"])
    plan = backup.plan_restore(snapshot, dst, "take_theirs")
    original_replace = Path.replace
    blocked = []
    writer = """
import sys
from pathlib import Path
from music.settings import write_music_enabled
try:
    write_music_enabled(Path(sys.argv[1]), True)
except OSError:
    sys.exit(23)
"""

    def replace(path: Path, target: Path) -> Path:
        if target == conf:
            other = subprocess.run([sys.executable, "-c", writer, str(dst.game)], timeout=20)
            blocked.append(other.returncode)
        return original_replace(path, target)

    monkeypatch.setattr(restore, "_game_running", lambda _: False)
    with patch.object(Path, "replace", replace):
        backup.execute(plan)
    assert blocked == [23], f"Second writer returned {blocked}, restore left {conf.read_bytes()!r}"
    subprocess.run([sys.executable, "-c", writer, str(dst.game)], timeout=20, check=True)
    assert conf.read_bytes() == b"nickname = incoming\nenable_music = 1\n"


def test_restore_inside_appearance_transaction(pair: tuple, snapshot: Path) -> None:
    src, dst = pair
    put(src, "game/deck/a.ydk")
    backup.create(src, snapshot)
    with appearance_lock(dst.game):
        tx = backup.execute(backup.plan_restore(snapshot, dst))
        assert dst.target("game/deck/a.ydk").is_file()
        backup.recover(dst, tx.id, "rollback")


@pytest.mark.parametrize(
    "target,selection,folder,lock",
    [
        ("studio/sleeves/example.png", "studio", "sleeves", ".library.lock"),
        ("studio/soundtracks/local/duel/a.wav", "audio", "soundtracks", ".import.lock"),
    ],
)
def test_restore_and_undo_respect_library_writers(
    pair: tuple, snapshot: Path, target: str, selection: str, folder: str, lock: str
) -> None:
    src, dst = pair
    put(src, target)
    backup.create(src, snapshot, [selection])
    plan = backup.plan_restore(snapshot, dst)
    with locked(dst.data / folder, lock), pytest.raises(OSError):
        backup.execute(plan)
    assert not dst.target(target).exists() and not backup.transactions(dst)
    tx = backup.execute(plan)
    with locked(dst.data / folder, lock), pytest.raises(OSError):
        backup.recover(dst, tx.id, "rollback")
    assert dst.target(target).exists()
    backup.recover(dst, tx.id, "rollback")
    assert not dst.target(target).exists()


def test_undo_reports_blocking_edit_before_publication(pair: tuple, snapshot: Path) -> None:
    src, dst = pair
    name = "game/deck/list.ydk"
    put(src, name)
    backup.create(src, snapshot)
    tx = backup.execute(backup.plan_restore(snapshot, dst))
    assert backup.preview_undo(dst, tx.id).available
    edited = put(dst, name, b"later edit")
    result = backup.preview_undo(dst, tx.id)
    assert not result.available and result.target == name
    assert "changed" in result.reason
    assert edited.read_bytes() == b"later edit" and backup.transactions(dst) == (tx,)


@pytest.mark.parametrize(
    "folder,name,lock",
    [
        ("sleeves/card-art", "123.jpg", ".fetch.lock"),
        ("themes/images/packs", "index.json", ".media.lock"),
    ],
)
def test_legacy_managed_files_respect_their_writer(
    pair: tuple, snapshot: Path, folder: str, name: str, lock: str
) -> None:
    _, dst = pair
    target = f"studio/{folder}/{name}"
    legacy_archive(snapshot, {target: ("studio", b"legacy")})
    plan = backup.plan_restore(snapshot, dst)
    with locked(dst.data / folder, lock), pytest.raises(OSError):
        backup.execute(plan)
    assert not dst.target(target).exists()

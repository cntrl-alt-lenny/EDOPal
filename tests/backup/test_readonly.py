import json
import os
import stat
from pathlib import Path

import pytest

import backup

from .test_transfer import put

windows_attributes = pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute")


@pytest.fixture
def plan(pair: tuple, snapshot: Path) -> backup.Plan:
    src, dst = pair
    for name in ("Alpha", "Beta"):
        put(src, f"game/deck/{name}.ydk", b"backup")
        put(dst, f"game/deck/{name}.ydk", b"local")
    backup.create(src, snapshot)
    return backup.plan_restore(snapshot, dst, "take_theirs")


@windows_attributes
def test_readonly_refused_before_staging(plan: backup.Plan) -> None:
    beta = plan.install.game / "deck/Beta.ydk"
    beta.chmod(stat.S_IREAD)
    reports = []
    try:
        with pytest.raises(
            PermissionError, match=r"Beta\.ydk is read-only\. Clear the read-only attribute"
        ):
            backup.execute(plan, progress=lambda *args: reports.append(args))
        assert not reports and not backup.transactions(plan.install)
        assert (beta.parent / "Alpha.ydk").read_bytes() == beta.read_bytes() == b"local"
        assert beta.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        beta.chmod(stat.S_IWRITE)
    assert backup.execute(plan).status == "complete"


def test_file_mode_applied_after_replace(
    plan: backup.Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = plan.install.game / "deck/Alpha.ydk"
    target.chmod(0o640)
    mode = target.stat().st_mode
    events = []
    chmod, replace = Path.chmod, Path.replace

    def record_replace(path: Path, destination: Path) -> Path:
        if destination == target:
            events.append("replace")
            assert path.stat().st_mode & stat.S_IWUSR
        return replace(path, destination)

    def record_chmod(path: Path, value: int) -> None:
        if path == target:
            events.append("chmod")
            assert path.read_bytes() == b"backup"
        chmod(path, value)

    monkeypatch.setattr(Path, "replace", record_replace)
    monkeypatch.setattr(Path, "chmod", record_chmod)
    backup.execute(plan)
    assert events == ["replace", "chmod"]
    assert target.stat().st_mode == mode


@windows_attributes
def test_attribute_changes_during_publication(plan: backup.Plan) -> None:
    directory = plan.install.game / "deck"
    beta = directory / "Beta.ydk"

    def mark_readonly(done: int, total: int, name: str) -> None:
        if done > len(plan.manifest.entries) and name.endswith("Alpha.ydk"):
            beta.chmod(stat.S_IREAD)

    try:
        with pytest.raises(PermissionError):
            backup.execute(plan, progress=mark_readonly)
        assert not list(directory.glob(".edopro-restore-*.tmp"))
        assert (directory / "Alpha.ydk").read_bytes() == b"backup"
        assert beta.read_bytes() == b"local"
        (tx,) = backup.transactions(plan.install)
        # Beta already has its original bytes. Rolling back only needs to change Alpha.
        assert backup.recover(plan.install, tx.id, "rollback").status == "rolled_back"
        assert (directory / "Alpha.ydk").read_bytes() == b"local"
    finally:
        beta.chmod(stat.S_IWRITE)


@windows_attributes
def test_failed_replace_cleans_readonly_temp(
    plan: backup.Plan, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = plan.install.game / "deck/Alpha.ydk"
    replace = Path.replace

    def fail_publish(path: Path, destination: Path) -> Path:
        if destination == target:
            path.chmod(stat.S_IREAD)
            raise PermissionError("publication refused")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(PermissionError, match="publication refused"):
        backup.execute(plan)
    assert not list(target.parent.glob(".edopro-restore-*.tmp"))
    assert target.read_bytes() == b"local"


def interrupted(plan: backup.Plan) -> tuple[backup.Transaction, Path]:
    alpha = plan.install.game / "deck/Alpha.ydk"
    with pytest.raises(backup.Cancelled):
        backup.execute(plan, cancel=lambda: alpha.read_bytes() == b"backup")
    (tx,) = backup.transactions(plan.install)
    temp = alpha.with_name(f".edopro-restore-{tx.id}.tmp")
    temp.write_bytes(b"unpublished")
    return tx, temp


@windows_attributes
@pytest.mark.parametrize("state", ["pending", "rolling_back"])
def test_rollback_old_readonly_temp(plan: backup.Plan, state: str) -> None:
    tx, temp = interrupted(plan)
    if state == "rolling_back":
        (record,) = plan.install.data.rglob("journal.json")
        doc = json.loads(record.read_bytes())
        doc["status"] = state
        record.write_text(json.dumps(doc), encoding="utf-8")
    temp.chmod(stat.S_IREAD)
    try:
        assert backup.preview_undo(plan.install, tx.id).available
        assert temp.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert backup.recover(plan.install, tx.id, "rollback").status == "rolled_back"
        assert not temp.exists()
        assert all(p.read_bytes() == b"local" for p in temp.parent.glob("*.ydk"))
        assert backup.execute(plan).status == "complete"
    finally:
        if temp.exists():
            temp.chmod(stat.S_IWRITE)


@windows_attributes
def test_undo_preview_detects_open_readonly_temp(plan: backup.Plan) -> None:
    tx, temp = interrupted(plan)
    temp.chmod(stat.S_IREAD)
    try:
        with temp.open("rb"):
            preview = backup.preview_undo(plan.install, tx.id)
            assert not preview.available and temp.name in preview.reason
            assert temp.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
            with pytest.raises(PermissionError, match="Cannot remove the restore temporary file"):
                backup.recover(plan.install, tx.id, "rollback")
            assert backup.transactions(plan.install)[0].status == "pending"
        assert backup.preview_undo(plan.install, tx.id).available
        assert backup.recover(plan.install, tx.id, "rollback").status == "rolled_back"
    finally:
        if temp.exists():
            temp.chmod(stat.S_IWRITE)

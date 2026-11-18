import json
import os
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

import backup
from backup.inventory import Source
from studio.pages.backup_data import (
    check_destination,
    comparison_rows,
    date_text,
    restore_review,
    totals,
)


def test_archive_dates_and_legacy_manifest(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "deck").mkdir(parents=True)
    put = game / "deck/Example.ydk"
    put.write_bytes(b"deck")
    stamp = 1_700_000_000
    os.utime(put, (stamp, stamp))
    install = backup.Installation(game, tmp_path)
    original = tmp_path / "saved.zip"
    legacy = tmp_path / "legacy.zip"
    created = backup.create(install, original)
    assert backup.inspect(original) == created
    assert created.created_at is not None and created.entries[0].modified_at == stamp
    with ZipFile(original) as source, ZipFile(legacy, "w", ZIP_STORED) as destination:
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(data)
                del manifest["created_at"]
                del manifest["entries"][0]["modified_at"]
                data = json.dumps(manifest).encode()
            destination.writestr(name, data)
    old = backup.inspect(legacy)
    assert old.created_at is None and old.entries[0].modified_at is None
    assert totals(old) == {"decks": backup.Total(1, 4), "replays": backup.Total(0, 0)}
    put.write_bytes(b"my own deck")
    conflict = restore_review(backup.plan_restore(legacy, install)).groups["conflict"][0]
    assert "Backup · 4 B, date unavailable" in conflict.detail
    assert f"Here · 11 B, {date_text(int(put.stat().st_mtime))}" in conflict.detail


def test_save_verifies_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game = tmp_path / "game"
    (game / "deck").mkdir(parents=True)
    (game / "deck/Example.ydk").write_bytes(b"deck")
    target = tmp_path / "saved.zip"
    real_copy = Source.copy_to

    def mismatched(source: Source, *args: object) -> tuple[str, int | None]:
        _, stamp = real_copy(source, *args)
        return "a" * 64, stamp

    monkeypatch.setattr(Source, "copy_to", mismatched)
    with pytest.raises(ValueError, match="did not pass verification"):
        backup.create(backup.Installation(game, tmp_path), target)
    assert not target.exists() and not tuple(tmp_path.glob(".backup-*"))


def test_archive_destination_protects_game_directory(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    install = backup.Installation(game, tmp_path)
    with pytest.raises(ValueError, match="outside your EDOPro folder"):
        check_destination(game / "backup.zip", install)
    assert check_destination(tmp_path / "backup.zip", install) == tmp_path / "backup.zip"


def test_game_can_open_during_preparation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "deck").mkdir(parents=True)
    (source / "deck/Example.ydk").write_bytes(b"deck")
    target.mkdir()
    archive = tmp_path / "backup.zip"
    backup.create(backup.Installation(source, tmp_path / "source-data"), archive)
    install = backup.Installation(target, tmp_path / "target-data")
    monkeypatch.setattr("backup.restore._game_running", lambda _: True)
    with pytest.raises(backup.GameRunning):
        backup.execute(backup.plan_restore(archive, install), require_closed=True)
    assert not (target / "deck").exists() and not backup.transactions(install)


def test_comparison_groups_keep_both_spellings_and_sides() -> None:
    compare = comparison_rows(
        (
            backup.Difference("game/deck/Example.ydk", "left", None, "case conflict"),
            backup.Difference("game/deck/EXAMPLE.ydk", None, "right", "case conflict"),
            backup.Difference("preferences/nickname", "left", "right", "changed"),
        )
    )
    here, incoming = compare.groups["case conflict"]
    assert "installation" in here.detail and here.title.endswith("Example")
    assert "backup" in incoming.detail and incoming.title.endswith("EXAMPLE")
    assert compare.groups["changed"][0].title == "Game preferences / Name in duels"

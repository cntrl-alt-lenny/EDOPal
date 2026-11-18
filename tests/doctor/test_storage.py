from pathlib import Path

import pytest

from doctor import ScanCancelled, storage_breakdown


def test_counts_each_storage_category_and_excludes_nested_app_data(tmp_path: Path) -> None:
    app = tmp_path / "private-data"
    for path, size in (
        ("pics/field/1.jpg", 3),
        ("replay/a.yrp", 7),
        ("deck/saved.ydk", 2),
        ("private-data/soundtracks/a.flac", 13),
        ("private-data/extras-backups/hash/a", 12),
        ("cards.cdb", 9),
        ("EDOPro.exe", 4),
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
    report = storage_breakdown(tmp_path, data_dir=app)
    totals = {category.id: (category.files, category.bytes) for category in report.categories}
    assert totals["artwork"] == (1, 3)
    assert totals["game_data"] == (1, 9)
    assert totals["other"] == (1, 4)
    assert totals["replays"] == (1, 7)
    assert totals["soundtracks"] == (1, 13)
    assert totals["backups"] == (1, 12)
    assert report.total_bytes == 50
    assert report.unreadable == ()


def test_cancel_during_traversal(tmp_path: Path) -> None:
    (tmp_path / "pics").mkdir()
    (tmp_path / "pics/1.jpg").write_bytes(b"123")
    calls = iter((False, False, True))
    with pytest.raises(ScanCancelled):
        storage_breakdown(tmp_path, cancelled=lambda: next(calls))

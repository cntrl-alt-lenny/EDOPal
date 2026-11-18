import hashlib
import json
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

import backup
from backup import archive as format_v1
from backup import plan as planner
from backup.core import MAX_FILE, Cancel, read_small
from backup.inventory import Source


def write_archive(
    path: Path,
    *,
    names: tuple[str, ...] = ("game/deck/a.ydk",),
    version: int = 1,
    method: int = ZIP_STORED,
    mode: int = stat.S_IFREG,
    content: bytes = b"deck",
    digest: str | None = None,
) -> None:
    rows = []
    with ZipFile(path, "w") as archive:
        for name in names:
            rows.append(
                {
                    "path": name,
                    "category": "decks",
                    "size": len(content),
                    "sha256": digest or hashlib.sha256(content).hexdigest(),
                }
            )
            info = ZipInfo(name)
            info.compress_type = method
            info.external_attr = (mode | 0o644) << 16
            archive.writestr(info, content)
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format_version": version,
                    "app_version": "5.5.0",
                    "source_platform": "linux",
                    "edopro_version": None,
                    "categories": ["decks"],
                    "entries": rows,
                }
            ),
        )


@pytest.mark.parametrize(
    "name",
    [
        "../outside.ydk",
        "game/deck/../outside.ydk",
        "/outside.ydk",
        r"C:\outside.ydk",
        "game/deck/C:outside.ydk",
        "game/deck/CON.ydk",
        "game/deck/aux .ydk",
        "game/deck/trailing./foo.ydk",
        "game/deck/a.ydk:stream",
        "game/deck/x\\a.ydk",
        "game/deck/.git/config.ydk",
        "game/deck/alternate~1/a.ydk",
        "game/script/test.lua",
        "game/config/system.conf",
        "game/puzzles/Canon collection/test.lua",
        "studio/restore-journals/example/journal.json",
    ],
)
def test_reject_path(snapshot: Path, name: str) -> None:
    write_archive(snapshot, names=(name,))
    with pytest.raises(ValueError):
        backup.inspect(snapshot)


def test_future_format(snapshot: Path) -> None:
    write_archive(snapshot, version=9000)
    with pytest.raises(ValueError, match="Unsupported backup format"):
        backup.inspect(snapshot)


def test_link_member(snapshot: Path) -> None:
    write_archive(snapshot, mode=stat.S_IFLNK)
    with pytest.raises(ValueError, match="regular files"):
        backup.inspect(snapshot)


def test_compressed_bomb(snapshot: Path) -> None:
    write_archive(snapshot, method=ZIP_DEFLATED, content=b"0" * (2 << 20))
    assert snapshot.stat().st_size < 4096
    with pytest.raises(ValueError, match="stored"):
        backup.inspect(snapshot)


def test_absurd_count_before_zipfile(snapshot: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_archive(snapshot)
    data = bytearray(snapshot.read_bytes())
    data[-14:-10] = b"\xff" * 4
    snapshot.write_bytes(data)
    monkeypatch.setattr(format_v1, "ZipFile", lambda *args: pytest.fail("Parsed oversized ZIP"))
    with pytest.raises(ValueError, match="directory"):
        backup.inspect(snapshot)


@pytest.mark.parametrize("corruption", ["hash", "crc"])
@pytest.mark.parametrize("selected", [[], ["decks"]])
def test_corrupt_including_excluded(
    pair: tuple, snapshot: Path, corruption: str, selected: list
) -> None:
    _, dest = pair
    write_archive(
        snapshot, content=b"unique-deck-data", digest="0" * 64 if corruption == "hash" else None
    )
    if corruption == "crc":
        snapshot.write_bytes(
            snapshot.read_bytes().replace(b"unique-deck-data", b"broken-deck-data")
        )
    plan = backup.plan_restore(snapshot, dest, categories=selected)
    with pytest.raises(ValueError, match="checksum|Damaged"):
        backup.execute(plan)
    assert not dest.target("game/deck").exists()
    assert not backup.transactions(dest)


def test_two_source_spellings(pair: tuple, snapshot: Path) -> None:
    _, dest = pair
    write_archive(
        snapshot,
        names=(
            "game/deck/A.ydk",
            "game/deck/a.ydk",
            "game/deck/GOAT/B.ydk",
            "game/deck/goat/C.ydk",
        ),
    )
    plan = backup.plan_restore(snapshot, dest, "keep_both")
    assert len({i.target.casefold() for i in plan.items}) == 4
    assert sum("collide" in i.detail for i in plan.items) == 2
    backup.execute(plan)
    assert all(dest.target(i.target).read_bytes() == b"deck" for i in plan.items)


def test_destination_repo_protected(pair: tuple, snapshot: Path) -> None:
    src, dest = pair
    puzzle = src.target("game/puzzles/A/test.lua")
    puzzle.parent.mkdir(parents=True)
    puzzle.write_bytes(b"personal")
    backup.create(src, snapshot, ["puzzles"])
    config = dest.target("game/config/user_configs.json")
    config.parent.mkdir()
    config.write_text('{"repos":[{"repo_path":"./puzzles/a"}]}')
    with pytest.raises(ValueError, match="Managed puzzle"):
        backup.plan_restore(snapshot, dest)


def test_oversize_compare_refused_before_read(pair: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    src, dst = pair
    path = src.target("game/deck/large.ydk")
    path.parent.mkdir()
    with path.open("wb") as file:
        file.truncate(MAX_FILE + 1)
    monkeypatch.setattr(Source, "open", lambda *_: pytest.fail("Opened oversized payload"))
    with pytest.raises(ValueError, match="limit"):
        backup.compare(src, dst)


def test_metadata_budget_before_read(snapshot: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot.write_bytes(b"too large")
    monkeypatch.setattr(Path, "open", lambda *_: pytest.fail("Opened oversized metadata"))
    with pytest.raises(ValueError, match="large"):
        read_small(snapshot, 8)


def test_merged_index_exceeds_track_budget(pair: tuple, snapshot: Path) -> None:
    src, _ = pair
    folder = src.target("studio/soundtracks")
    (folder / "local/duel").mkdir(parents=True)
    (folder / "local/duel/new.ogg").write_bytes(b"track")
    tracks = [f"local/duel/{i}.ogg" for i in range(format_v1.MAX_FILES)]
    (folder / "index.json").write_text(json.dumps(tracks))
    with pytest.raises(ValueError, match="Too many"):
        backup.create(src, snapshot, ["studio"])
    assert not snapshot.exists()


def test_preview_bounds_existing_files_before_hashing(
    pair: tuple, snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src, dst = pair
    for installation, content in [(src, b"x"), (dst, b"local")]:
        folder = installation.target("game/deck")
        folder.mkdir()
        for name in ("a.ydk", "b.ydk"):
            (folder / name).write_bytes(content)
    backup.create(src, snapshot)
    monkeypatch.setattr(planner, "MAX_TOTAL", 8)
    original_signature = planner.signature

    def bounded_signature(path: Path, cancel: Cancel) -> str | None:
        assert path.name != "b.ydk", "Read beyond the cumulative preview budget"
        return original_signature(path, cancel)

    monkeypatch.setattr(planner, "signature", bounded_signature)
    with pytest.raises(ValueError, match="limit"):
        backup.plan_restore(snapshot, dst)

import hashlib
import json
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

import backup
from backup import archive as format_v1


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

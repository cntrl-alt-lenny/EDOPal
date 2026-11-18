import hashlib
import io
import json
import stat
import struct
import zlib
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from PySide6.QtGui import QColor, QImage

from themes import PRESETS, Theme, ThemeLibrary
from themes.imaging import png

BASE = {
    "version": 1,
    "name": "Sea glass",
    "background": "#151525",
    "surface": "#29293f",
    "accent": "#eee988",
}


def archive(path: Path, members: dict) -> Path:
    with ZipFile(path, "w") as share:
        for key, value in members.items():
            share.writestr(key, value)
    return path


@pytest.mark.parametrize(
    "update",
    [
        {"version": 2},
        {"version": True},
        {"depth": 101},
        {"roundness": -1},
        {"roundness": 2.5},
        {"highlight_turn": 1},
        {"surface": "transparent"},
        {"name": "../skin"},
        {"name": "NUL.txt"},
        {"name": "broken\nwindow"},
        {"name": "x" * 65},
        {"new_field": 1},
        {"font": "remote.ttc"},
        {"image": "../cover.png"},
        {"wallpaper": "image"},
        {"muted": "#ffffff00"},
        {"wallpaper": "image", "image": "http://my.photo"},
        {"accent": None},
    ],
)
def test_reject_invalid_schema(update: dict) -> None:
    with pytest.raises(ValueError):
        Theme.from_json(json.dumps(BASE | update).encode())


def test_reject_malformed_definitions() -> None:
    for raw in (
        b"{",
        b"\xff",
        b"[]",
        b"{}",
        b"x" * 4097,
        json.dumps(BASE).replace('"version": 1', '"version": 1, "version": 1').encode(),
    ):
        with pytest.raises(ValueError):
            Theme.from_json(raw)
    assert (
        Theme.from_json(Theme.from_json(json.dumps(BASE).encode()).to_json()).name == BASE["name"]
    )


def test_library_photo_survives_move_and_can_duplicate(tmp_path: Path) -> None:
    lib = ThemeLibrary(tmp_path / "library")
    assert lib.entries() == {}
    image = QImage(45, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#763940"))
    photo = tmp_path / "photo with spaces.jpg"
    assert image.save(str(photo), "JPEG")
    digest = lib.add_image(photo)
    t = replace(PRESETS[0], name="海 & Sky", wallpaper="image", image=digest)
    theme_id = lib.save(t)
    share = tmp_path / "test.edotheme"
    lib.export_file(theme_id, share)
    photo.unlink()
    other = ThemeLibrary(tmp_path / "second")
    imported = other.import_file(share)
    assert imported != theme_id and other.load(imported) == t
    assert other.image(t) == lib.image(t)
    assert {theme_id: t} == ThemeLibrary(lib.directory).entries()
    assert other.import_file(share) != imported
    assert other.save(replace(t, depth=73), imported) == imported
    assert other.load(imported).depth == 73
    simple = other.save(PRESETS[1])
    other.export_file(simple, share)
    assert lib.load(lib.import_file(share)) == PRESETS[1]
    with pytest.raises(ValueError):
        lib.save(PRESETS[0], "../outside")


def test_untrusted_archive_member_and_image_bounds(tmp_path: Path) -> None:
    lib = ThemeLibrary(tmp_path / "saved")
    share = tmp_path / "bad.edotheme"
    good = PRESETS[0].to_json()
    img = QImage(1, 1, QImage.Format.Format_RGB32)
    img.fill(QColor("green"))
    payload = bytearray(png(img))
    # Plausible PNG signature and valid IHDR CRC with an excessive decoded size.
    payload[16:24] = struct.pack(">II", 16000, 16000)
    payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]))
    photo_theme = replace(PRESETS[0], wallpaper="image", image=hashlib.sha256(payload).hexdigest())
    link = ZipInfo("theme.json")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for entries in (
        {"../outside.json": good},
        {"theme.json": good, "C:/boot.ini": b"x"},
        {"theme.json": b"x" * 5000},
        {link: good},
        {"theme.json": good, "wallpaper": b"stray"},
        {"theme.json": photo_theme.to_json()},
        {"theme.json": photo_theme.to_json(), "wallpaper": payload},
        {"theme.json": photo_theme.to_json(), "wallpaper": b"bad"},
        {"theme.json": good, "a": b"1", "b": b"2"},
    ):
        archive(share, entries)
        with pytest.raises(ValueError):
            lib.import_file(share)
    with ZipFile(share, "w", ZIP_DEFLATED) as pack:
        pack.writestr("theme.json", b" " * 1_000_000)
    with pytest.raises(ValueError):
        lib.import_file(share)
    with share.open("wb") as handle:
        handle.truncate(22 * 1024 * 1024)
    with pytest.raises(ValueError):
        lib.import_file(share)
    assert not lib.directory.exists() and not (tmp_path / "outside.json").exists()
    with pytest.raises(ValueError):
        lib.add_image(share)


def test_malformed_zip_and_checksum(tmp_path: Path) -> None:
    lib = ThemeLibrary(tmp_path / "library")
    path = tmp_path / "malformed"
    out = io.BytesIO()
    with ZipFile(out, "w") as share:
        share.writestr("theme.json", PRESETS[0].to_json())
    for raw in (
        b"PK",
        b"x" * 4096,
        out.getvalue().replace(b"Current", b"Corrupt"),
        out.getvalue()[:-3],
    ):
        path.write_bytes(raw)
        with pytest.raises(ValueError):
            lib.import_file(path)
    assert not lib.directory.exists()

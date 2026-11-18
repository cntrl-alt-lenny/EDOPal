"""Version-one folder/ZIP layouts shared by release staging and library installation."""

import json
import re
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from music.audio import FORMATS, validate_audio
from music.catalogue import SCENES, official_games
from sleeves.imaging import decode

from .archives import pack_files
from .download import check_cancel
from .media import Item

MAX_ITEMS = 256
MAX_FILES = 2 * MAX_ITEMS + 1  # Tools may emit an explicit directory per item.
MAX_AUDIO = 128 * 1024**2
MAX_IMAGE = 16 * 1024**2
MAX_TOTAL = 512 * 1024**2
MAX_METADATA = 256 * 1024
IMAGE_TYPES = {".jpg", ".jpeg", ".png"}
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}")


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or not value.isprintable()
    ):
        raise ValueError("Titles and tags need 1–128 printable characters.")
    return value.strip()


def _strings(values: object, limit: int) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > limit:
        raise ValueError("Expected a bounded list of labels.")
    result = tuple(_text(value) for value in values)
    if len(set(result)) != len(result):
        raise ValueError("Repeated label.")
    return result


def _fields(row: dict, required: set[str], optional: frozenset[str] = frozenset()) -> None:
    if not required <= row.keys() <= required | optional:
        raise ValueError("Unexpected or missing pack.json item fields.")


def _game(name: str) -> str:
    if name not in official_games():
        raise ValueError(f"Unknown catalogue game ID: {name}")
    return name


def _cover(row: dict, path: Path) -> Item:
    _fields(row, {"file"})
    if path.stem != "cover" or path.suffix.lower() not in IMAGE_TYPES:
        raise ValueError("Expected <game>/cover.png or a JPEG cover.")
    return Item(row["file"], "Cover", "cover", _game(path.parent.name))


def _music(row: dict, path: Path) -> Item:
    if path.suffix.lower() in IMAGE_TYPES:
        return _cover(row, path)
    _fields(row, {"file", "scenes"}, frozenset({"title"}))
    scenes = _strings(row["scenes"], len(SCENES))
    if not scenes or not set(scenes) <= SCENES.keys() or path.suffix.lower() not in FORMATS:
        raise ValueError("Choose supported audio and at least one known scene.")
    return Item(
        row["file"], _text(row.get("title", path.stem)), "track", _game(path.parent.name), scenes
    )


def _image(row: dict, path: Path, *, sleeve: bool) -> Item:
    if path.parent.name != "images" or path.suffix.lower() not in IMAGE_TYPES:
        raise ValueError("Expected a PNG or JPEG under images/.")
    if sleeve:
        _fields(row, {"file", "name", "tags"})
        return Item(row["file"], _text(row["name"]), "sleeve", tags=_strings(row["tags"], 24))
    _fields(row, {"file", "title"})
    return Item(row["file"], _text(row["title"]), "wallpaper")


LAYOUTS: dict[str, Callable[[dict, Path], Item]] = {
    "covers": _cover,
    "music": _music,
    "sleeves": lambda row, path: _image(row, path, sleeve=True),
    "wallpaper": lambda row, path: _image(row, path, sleeve=False),
}


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON key in pack.json.")
    return result


def _directory(path: Path) -> None:
    # ZipFile allocates every central directory entry before exposing infolist().
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        if not 22 <= size <= MAX_TOTAL + 1024**2:
            raise ValueError("Invalid library ZIP size.")
        stream.seek(-22, 2)
        sig, disk, start, count, total, length, offset, comment = struct.unpack(
            "<4s4H2IH", stream.read(22)
        )
        if (
            sig != b"PK\x05\x06"
            or disk
            or start
            or count != total
            or not 1 <= count <= MAX_FILES
            or length > MAX_METADATA
            or offset + length + 22 != size
            or comment
        ):
            raise ValueError("Invalid or oversized library ZIP directory.")
        if size >= 42:
            stream.seek(-42, 2)
            if stream.read(4) == b"PK\x06\x07":
                raise ValueError("ZIP64 library packs are unsupported.")


def _items(archive: ZipFile, kind: str) -> list[Item]:
    files = pack_files(archive)
    infos = archive.infolist()
    if (
        len(infos) > MAX_FILES
        or sum(m.file_size for m in infos) > MAX_TOTAL
        or any(m.compress_type not in {ZIP_STORED, ZIP_DEFLATED} for m in infos)
        or any(m.filename == "duplicates.json" for m in infos)
        or "pack.json" not in files
        or archive.getinfo("pack.json").file_size > MAX_METADATA
    ):
        raise ValueError("Expected a bounded library ZIP with pack.json.")
    metadata = json.loads(archive.read("pack.json").decode("utf-8"), object_pairs_hook=_unique)
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"items"}
        or not isinstance(metadata["items"], list)
        or not 1 <= len(metadata["items"]) <= MAX_ITEMS
    ):
        raise ValueError("pack.json needs 1–256 items.")
    items = []
    for row in metadata["items"]:
        name = row.get("file") if isinstance(row, dict) else None
        if (
            not isinstance(name, str)
            or name not in files
            or name.count("/") != 1
            or not _FILENAME.fullmatch(name.split("/")[1])
        ):
            raise ValueError("Each item must name an existing file one folder deep.")
        item = LAYOUTS[kind](row, Path(name))
        if (
            not 0
            < archive.getinfo(name).file_size
            <= (MAX_AUDIO if item.role == "track" else MAX_IMAGE)
        ):
            raise ValueError(f"Empty or oversized media: {name}")
        items.append(item)
    names = {item.member for item in items}
    parents = {name.split("/")[0] + "/" for name in names}
    if (
        len(names) != len(items)
        or names | {"pack.json"} != files.keys()
        or any(m.is_dir() and m.orig_filename not in parents for m in infos)
    ):
        raise ValueError("Repeated or undeclared files or folders in the pack.")
    covers = [i.game for i in items if i.role == "cover"]
    games = {i.game for i in items if i.role == "track"}
    if len(set(covers)) != len(covers) or (kind == "music" and (not games or set(covers) - games)):
        raise ValueError(
            "A music pack needs recordings for each included game and at most one cover."
        )
    return items


@contextmanager
def staged_items(
    path: Path, kind: str, work: Path, *, cancel: Event | None = None
) -> Iterator[list[tuple[Item, Path]]]:
    """Validate all members before handing any file to a library. Use generated local names."""
    check_cancel(cancel)
    _directory(path)
    with TemporaryDirectory(dir=work) as temporary:
        result = []
        try:
            with ZipFile(path) as archive:
                for i, item in enumerate(_items(archive, kind)):
                    ext = Path(item.member).suffix.lower()
                    source = Path(temporary) / f"{i}{ext}"
                    with archive.open(item.member) as stream, source.open("xb") as out:
                        while chunk := stream.read(128 * 1024):
                            check_cancel(cancel)
                            out.write(chunk)
                    if item.role == "track":
                        validate_audio(source)
                    else:
                        data = source.read_bytes()
                        if data.startswith(b"\x89PNG\r\n\x1a\n") != (ext == ".png"):
                            raise ValueError(
                                f"Image extension disagrees with contents: {item.member}"
                            )
                        decode(data)
                    result.append((item, source))
        except (BadZipFile, RecursionError, EOFError) as exc:
            raise ValueError("Damaged library archive.") from exc
        yield result

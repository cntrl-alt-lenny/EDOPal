"""Version 1 is a bounded, uncompressed ZIP with an independently readable JSON manifest."""

import json
import os
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from time import time
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from config import app_version

from . import collection
from .core import (
    DEFAULTS,
    MAX_FILE,
    MAX_FILES,
    MAX_MANIFEST,
    MAX_TOTAL,
    Cancel,
    Digest,
    Installation,
    Progress,
    check,
    publish_new,
    read_small,
    transfer,
)
from .inventory import bounded_files, category, file_limit, selection
from .preferences import LIMIT

MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Entry:
    path: str
    category: str
    size: int
    sha256: str
    modified_at: int | None = None


@dataclass(frozen=True)
class Manifest:
    format_version: int
    source_platform: str
    edopro_version: str | None
    app_version: str
    categories: tuple[str, ...]
    entries: tuple[Entry, ...]
    created_at: int | None = None


def game_version(install: Installation) -> str | None:
    if sys.platform == "win32":
        exe = install.target("game/EDOPro.exe")
        if exe.is_file():
            try:
                probe = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "(Get-Item -LiteralPath $env:EDOPRO_VERSION_PROBE).VersionInfo.FileVersion",
                    ],
                    env={**os.environ, "EDOPRO_VERSION_PROBE": str(exe)},
                    timeout=8,
                    capture_output=True,
                    check=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return probe.stdout.decode("ascii", "replace").strip()[:100] or None
            except (OSError, subprocess.SubprocessError):
                pass
    for rel in ("EDOPro.app/Contents/Info.plist", "Contents/Info.plist"):
        plist = install.target("game/" + rel)
        if plist.is_file():
            try:
                version = plistlib.loads(read_small(plist, LIMIT)).get("CFBundleShortVersionString")
                if isinstance(version, str):
                    return version[:100]
            except (ValueError, plistlib.InvalidFileException):
                pass
    return None


def create(
    install: Installation,
    destination: Path,
    categories: Iterable[str] = DEFAULTS,
    *,
    progress: Progress = None,
    cancel: Cancel = None,
) -> Manifest:
    """Publish at a new path only after the entire archive has been written."""
    selected = selection(categories)
    files = bounded_files(install, selected, cancel)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".backup-", delete=False
    ) as out:
        temp = Path(out.name)
    try:
        entries = []
        with ZipFile(temp, "w", allowZip64=False) as archive:
            for i, file in enumerate(files, 1):
                with archive.open(file.name, "w") as member:
                    digest, modified_at = file.copy_to(install, member, cancel)
                entries.append(
                    Entry(file.name, category(file.name, selected), file.size, digest, modified_at)
                )
                if progress:
                    progress(i, 2 * len(files), file.name)
            manifest = Manifest(
                1,
                sys.platform,
                game_version(install),
                app_version(),
                tuple(sorted(selected)),
                tuple(entries),
                int(time()),
            )
            data = json.dumps(asdict(manifest), ensure_ascii=False).encode()
            if len(data) > MAX_MANIFEST:
                raise ValueError("Manifest too large")
            archive.writestr(MANIFEST, data)
        # A checksum of the source alone cannot prove that the saved copy is readable.
        with opened(temp) as (archive, saved):
            for i, entry in enumerate(saved.entries, len(files) + 1):
                with archive.open(entry.path) as stream:
                    if transfer(stream, None, entry.size, cancel) != (entry.size, entry.sha256):
                        raise ValueError("The saved backup did not pass verification")
                if progress:
                    progress(i, 2 * len(files), entry.path)
        check(cancel)
        publish_new(temp, destination)
        return manifest
    finally:
        temp.unlink(missing_ok=True)


def decode(data: bytes) -> dict:
    def unique(pairs: list[tuple]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        obj = json.loads(data, object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError("Too many nested JSON objects") from exc
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object")
    return obj


def timestamp(value: object) -> int | None:
    if value is None or (type(value) is int and -62135596800 <= value <= 253402300799):
        return value
    raise ValueError("Invalid backup date")


def parse(archive: ZipFile) -> Manifest:
    info = archive.infolist()
    names = {i.filename for i in info}
    if len(names) != len(info) or len(info) > MAX_FILES + 1 or MANIFEST not in names:
        raise ValueError("Missing manifest, duplicate members or too many files")
    if sum(i.file_size for i in info if i.filename != MANIFEST) > MAX_TOTAL:
        raise ValueError("Archive too large")
    for item in info:
        mode = stat.S_IFMT(item.external_attr >> 16)
        if (
            item.compress_type != ZIP_STORED
            or item.file_size != item.compress_size
            or item.file_size > MAX_FILE
            or item.orig_filename != item.filename
            or item.flag_bits & ~0x800
            or item.is_dir()
            or mode not in {0, stat.S_IFREG}
            or item.external_attr & 0x410
        ):
            raise ValueError("Only plain, stored regular files are supported")
    if archive.getinfo(MANIFEST).file_size > MAX_MANIFEST:
        raise ValueError("Manifest too large")
    doc = decode(archive.read(MANIFEST))
    if type(doc.get("format_version")) is not int or doc["format_version"] != 1:
        raise ValueError("Unsupported backup format version")
    for key in ("app_version", "source_platform", "edopro_version"):
        v = doc.get(key)
        if v is None and key == "edopro_version" and key in doc:
            continue
        if not isinstance(v, str) or not 0 < len(v) <= 128:
            raise ValueError(f"Invalid {key}")
    categories = doc.get("categories")
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        raise ValueError("Invalid categories")
    selected = selection(categories)
    rows = doc.get("entries")
    if not isinstance(rows, list) or len(rows) != len(info) - 1:
        raise ValueError("Manifest entry count mismatch")
    entries = []
    names.remove(MANIFEST)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid manifest entry")
        name = row.get("path")
        cat = category(name, selected)
        size, digest = row.get("size"), row.get("sha256")
        if (
            cat != row.get("category")
            or cat not in selected
            or name not in names
            or type(size) is not int
            or size != archive.getinfo(name).file_size
            or not isinstance(digest, str)
            or not Digest.fullmatch(digest)
            or (cat == "preferences" and size > 256)
            or size > file_limit(name)
            or collection.asset_digest(name) not in {None, digest}
        ):
            raise ValueError(f"Invalid manifest entry: {name}")
        names.remove(name)
        entries.append(Entry(name, cat, size, digest, timestamp(row.get("modified_at"))))
    return Manifest(
        1,
        doc["source_platform"],
        doc["edopro_version"],
        doc["app_version"],
        tuple(sorted(selected)),
        tuple(entries),
        timestamp(doc.get("created_at")),
    )


@contextmanager
def opened(path: Path) -> Iterator[tuple[ZipFile, Manifest]]:
    # ZipFile loads the entire central directory in its constructor. Bound it first.
    with Path(path).open("rb") as stream:
        length = stream.seek(0, os.SEEK_END)
        if length < 22 or length > MAX_TOTAL + 3 * MAX_MANIFEST:
            raise ValueError("Invalid archive size")
        stream.seek(-22, os.SEEK_END)
        sig, disk, cd_disk, n, count, size, start, comment = struct.unpack(
            "<4s4H2IH", stream.read()
        )
        if (
            sig != b"PK\x05\x06"
            or disk
            or cd_disk
            or comment
            or count != n
            or not 1 <= count <= MAX_FILES + 1
            or size > MAX_MANIFEST
            or start + size != length - 22
        ):
            raise ValueError("Invalid or oversized ZIP directory")
        stream.seek(start)
        directory = stream.read(size)
        offset = 0
        for _ in range(count):
            if directory[offset : offset + 4] != b"PK\x01\x02" or offset + 46 > size:
                raise ValueError("Invalid ZIP directory record")
            offset += 46 + sum(struct.unpack_from("<3H", directory, offset + 28))
        if offset != size or directory[-20:-16] == b"PK\x06\x07":
            raise ValueError("Invalid ZIP directory length")
        try:
            with ZipFile(stream) as archive:
                yield archive, parse(archive)
        except BadZipFile as exc:
            raise ValueError("Damaged backup archive") from exc


def inspect(path: Path) -> Manifest:
    with opened(path) as (_, manifest):
        return manifest

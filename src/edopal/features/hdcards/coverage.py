"""Measure visible artwork in the game client's search order."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
from collections.abc import Callable, Container
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from functools import cached_property, lru_cache, partial
from pathlib import Path
from stat import S_ISREG
from zipfile import BadZipFile, ZipFile

from PIL import Image

from edopal.core.install import Install

from .artwork import MAX_BYTES, MAX_PIXELS, image_dimensions
from .catalog import Catalog


def _identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _path_stamp(path):
    try:
        return _identity(os.stat(path))
    except FileNotFoundError:
        return None


class ChangedArtworkError(OSError):
    """A previously measured game file was moved, replaced or edited."""


@dataclass(frozen=True)
class Asset:
    path: Path
    member: str | None
    width: int
    height: int
    stamp: tuple[int, ...]
    _digest: str = field(default="", compare=False, repr=False)

    @property
    def is_hd(self):
        # Both the older ~421×614 images and current 813×1185 prints qualify.
        return self.width >= 400 and self.height >= 600

    @cached_property
    def digest(self):
        return self._digest or hashlib.sha256(self.read_bytes()).hexdigest()

    def read_bytes(self) -> bytes:
        try:
            stream = self.path.open("rb")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise ChangedArtworkError("Installed artwork changed during sync") from exc
        with stream as source:
            self._check_identity(source)
            if self.member is None:
                data = source.read(MAX_BYTES + 1)
            else:
                with ZipFile(source) as z, z.open(self.member) as member:
                    data = member.read(MAX_BYTES + 1)
            self._check_identity(source)
        if len(data) > MAX_BYTES:
            raise OSError("Artwork exceeds 25 MiB")
        return data

    def _check_identity(self, source):
        if _identity(os.fstat(source.fileno())) != self.stamp:
            raise ChangedArtworkError("Installed artwork changed during sync")


@dataclass
class Coverage:
    faces: dict[int, Asset] = field(default_factory=dict)
    fields: dict[int, Asset] = field(default_factory=dict)
    blocked: set[tuple[str, int]] = field(default_factory=set)
    root_png: set[tuple[str, int]] = field(default_factory=set)
    complete: bool = True

    @property
    def hd_faces(self):
        return {cid for cid, asset in self.faces.items() if asset.is_hd}

    def copy(self):
        return Coverage(self.faces.copy(), self.fields.copy(), self.blocked.copy(), self.root_png.copy(), self.complete)


def _decode(path, member, stamp, data):
    try:
        width, height = image_dimensions(data)
    except (OSError, ValueError, SyntaxError):
        return None
    return Asset(path, member, width, height, stamp, hashlib.sha256(data).hexdigest())


def _id(name):
    stem, _, ext = name.rpartition(".")
    if ext in ("png", "jpg") and len(stem) <= 10 and stem.isascii() and stem.isdecimal() and stem[0] != "0":
        return int(stem)
    return None


# Measurements are disposable. The key changes when the validation policy does.
_DECODER_VERSION = 2


@contextmanager
def _measurements(cache, root):
    db, known, changed = None, {}, []
    if cache is not None:
        try:
            key = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()
            path = cache / "hdcards" / f"coverage-{_DECODER_VERSION}-{key}.sqlite3"
            if not path.resolve().is_relative_to(root):
                path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(path, timeout=0.25)
                db.execute(
                    "CREATE TABLE IF NOT EXISTS images (path BLOB, member TEXT, stamp TEXT, width INTEGER, "
                    "height INTEGER, digest TEXT, PRIMARY KEY(path, member)) WITHOUT ROWID"
                )
                rows = db.execute("SELECT path, member, stamp, width, height, digest FROM images")
                known = {(p, m): (s, w, h, d) for p, m, s, w, h, d in rows if isinstance(w, int) and isinstance(h, int)}
        except (OSError, sqlite3.Error):
            if db is not None:
                db.close()
            db = None

    def measure(path, member, stamp, load, need_digest):
        if db is not None:
            # Each database already belongs to one canonical install root.
            key = os.fsencode(path), member or ""
            fingerprint = repr(stamp)  # Windows inode values can exceed SQLite's integer range.
            previous = known.get(key)
        else:
            previous = None
        if previous and previous[0] == fingerprint:
            _, width, height, digest = previous
            if not width:
                return None
            asset = Asset(path, member, width, height, stamp, digest)
            if digest or not need_digest:
                return asset
        else:
            asset = load()
        if asset and not asset._digest and (need_digest or not asset.is_hd):
            asset = replace(asset, _digest=asset.digest)
        if db is not None:
            values = (asset.width, asset.height, asset._digest) if asset else (0, 0, "")
            changed.append((*key, fingerprint, *values))
        return asset

    try:
        yield measure
    finally:
        if db is not None:
            with suppress(sqlite3.Error):
                if changed:
                    db.executemany("INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?)", changed)
                    db.commit()
            db.close()


_BATCH = 128


def _stable(stamps, cancelled):
    def unchanged(batch):
        if cancelled():
            raise CancelledError
        return all(_path_stamp(path) == stamp for path, stamp in batch)

    if len(stamps) < 512:
        return unchanged(stamps)
    # stat() releases the GIL. Small batches overlap filesystem latency without
    # scheduling a Future per image on a full installation.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return all(pool.map(unchanged, (stamps[i : i + 128] for i in range(0, len(stamps), 128))))


@dataclass(frozen=True)
class _Snapshot:
    scope: tuple
    art: Coverage
    stamps: tuple


# Refresh, Sync and its final inspection often check the same install back-to-back.
# A complete prior scan records every directory and visible candidate that affected it.
_last_scan: _Snapshot | None = None


def coverage(
    game: Install,
    catalog: Catalog,
    *,
    cache: Path | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    on_batch: Callable[[Coverage, tuple[int, ...]], None] | None = None,
    digest_ids: Container[tuple[int, str]] = (),
) -> Coverage:
    """Emit resolved IDs, including absent faces, while remaining local files are inspected.

    Each batch includes the finished field map so donors outside the batch are visible.
    Only the returned result certifies that the sources stayed stable through the scan.
    """
    global _last_scan
    result = Coverage()
    root = game.root.resolve()
    primary = root / "pics"
    measured = {}

    def check_cancel():
        if cancelled():
            raise CancelledError

    def emit(ids):
        if not ids or on_batch is None:
            return
        check_cancel()
        keys = {(kind, cid) for cid in ids for kind in ("face", "field")}
        on_batch(
            Coverage(
                faces={cid: result.faces[cid] for cid in ids if cid in result.faces},
                fields=result.fields.copy(),
                blocked=result.blocked & keys,
                root_png=result.root_png & keys,
            ),
            tuple(ids),
        )

    def scan_dir(folder, kind):
        images = result.faces if kind == "face" else result.fields
        ids = catalog.cards if kind == "face" else catalog.fields
        managed = folder == primary or folder == primary / "field"
        last_faces = managed and kind == "face" and on_batch is not None
        if not folder.resolve().is_relative_to(root):
            result.complete = False
            return
        measured[folder] = _path_stamp(folder)
        paths = {}
        if measured[folder] is not None:
            with os.scandir(folder) as entries:
                for entry in entries:
                    name = entry.name.lower()
                    cid = _id(name)
                    if cid in ids and cid not in images:
                        # The client asks for lowercase extensions, even on case-folding volumes.
                        if name == entry.name or (folder / name).is_file():
                            paths.setdefault(cid, []).append(entry)

        if last_faces:
            # At this point every possible path is known. Start missing downloads first.
            ready = sorted(ids.keys() - paths.keys())
            for start in range(0, len(ready), _BATCH):
                emit(ready[start : start + _BATCH])

        ready = []
        for cid, choices in paths.items():
            check_cancel()
            if len(choices) > 1:
                choices.sort(key=lambda entry: not entry.name.lower().endswith(".png"))
            for entry in choices:
                name = entry.name.lower()
                path = folder / name
                if managed and name.endswith(".png"):
                    result.root_png.add((kind, cid))
                # When both spellings exist the client opens the exact lowercase name.
                node = entry if name == entry.name else path
                if node.is_symlink() and not path.resolve().is_relative_to(root):
                    result.complete = False
                    continue
                try:
                    stat = node.stat()
                    stamp = _identity(stat)
                    measured[path] = stamp
                    if S_ISREG(stat.st_mode):
                        need_digest = kind == "field" or (cid, kind) in digest_ids
                        asset = measure(path, None, stamp, partial(_local, path, stamp), need_digest)
                        if asset:
                            images[cid] = asset
                            if not managed:
                                result.blocked.add((kind, cid))
                            break
                except FileNotFoundError:
                    result.complete = False
            if last_faces:
                ready.append(cid)
                if len(ready) == _BATCH:
                    emit(ready)
                    ready.clear()
        emit(ready)

    def scan_archives(kind, archives):
        images = result.faces if kind == "face" else result.fields
        ids = catalog.cards if kind == "face" else catalog.fields
        parent = "pics/" if kind == "face" else "pics/field/"
        found = {}
        for path, stamp, z in archives:
            for info in z.infolist():
                name = info.filename.casefold()
                if name.startswith(parent) and (cid := _id(name[len(parent) :])) in ids:
                    found.setdefault((cid, name[-3:]), (path, stamp, z, info))
        for (cid, _), (path, stamp, z, info) in sorted(found.items(), key=lambda item: item[0][1] != "png"):
            check_cancel()
            if cid not in images and info.file_size <= MAX_BYTES:

                def load(path=path, stamp=stamp, z=z, info=info):
                    return _decode(path, info.filename, stamp, z.read(info))

                if asset := measure(path, info.filename, stamp, load, True):
                    images[cid] = asset
                    result.blocked.add((kind, cid))

    try:
        snapshot = _last_scan
        # Small scopes cost less to enumerate directly. Avoid displacing the full scan
        # when checking a handful of staged files before publication.
        scope = None
        if len(catalog.cards) >= _BATCH:
            scope = (
                root,
                cache,
                frozenset(catalog.cards),
                frozenset(catalog.fields),
                tuple(catalog.repo_pics),
                tuple(catalog.archives),
            )
        if snapshot and snapshot.scope == scope and catalog.stable():
            has_digests = not digest_ids or all(
                asset._digest or (cid, "face") not in digest_ids for cid, asset in snapshot.art.faces.items()
            )
            if has_digests and _stable(snapshot.stamps, cancelled):
                result = snapshot.art.copy()
                if on_batch:
                    ordered = sorted(catalog.cards)
                    for start in range(0, len(ordered), _BATCH):
                        emit(ordered[start : start + _BATCH])
                    result.complete = catalog.stable() and _stable(snapshot.stamps, cancelled)
                check_cancel()
                return result

        with ExitStack() as stack:
            measure = stack.enter_context(_measurements(cache, root))
            archives = [(p, _identity(p.stat()), stack.enter_context(ZipFile(p))) for p in catalog.archives]
            measured.update((p, stamp) for p, stamp, _ in archives)
            # Fields are few and a face batch may need a field donor from another batch.
            for kind in ("field", "face"):
                sub = "" if kind == "face" else "field"
                if kind == "face":
                    for folder in catalog.repo_pics:
                        scan_dir(folder, kind)
                scan_dir(root / "expansions" / "pics" / sub, kind)
                scan_archives(kind, archives)
                scan_dir(primary / sub, kind)
        stamps = tuple(measured.items())
        result.complete &= catalog.stable() and _stable(stamps, cancelled)
        if scope and result.complete:
            _last_scan = _Snapshot(scope, result.copy(), stamps)
    except (BadZipFile, OSError, RuntimeError):
        result.complete = False
    check_cancel()
    return result


@lru_cache(maxsize=60_000)
def _local(path, stamp):
    size = stamp[2]
    if size > MAX_BYTES:
        return None
    with path.open("rb") as source:
        prefix = source.read(2)
        source.seek(0)
        if prefix != b"\xff\xd8":
            return _decode(path, None, stamp, source.read(MAX_BYTES + 1))
        # JPEG dimensions live before the scan data. Check the trailer as well, without
        # reading or expanding the compressed pixels of every installed card face.
        try:
            source.seek(-min(64, size), 2)
            if b"\xff\xd9" not in source.read():
                return None
            source.seek(0)
            with Image.open(source, formats=("JPEG",)) as image:
                if image.width * image.height <= MAX_PIXELS:
                    return Asset(path, None, *image.size, stamp)
        except (OSError, ValueError, SyntaxError, struct.error, Image.DecompressionBombError):
            pass
    return None

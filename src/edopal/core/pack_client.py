"""On-demand, verified release packs for the desktop app. Call from a worker thread."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import ssl
import stat
import struct
import sys
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager, nullcontext
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

import certifi

from edopal import PACKS_REPO
from edopal.core.locks import file_lock
from edopal.core.packs import PACK_KINDS, PackInfo, PackKind, Progress
from edopal.core.paths import Paths

CATALOG_URL = f"https://github.com/{PACKS_REPO}/releases/download/catalog/catalog.json"
MAX_MEMBERS = 4096
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_FILE_BYTES = 512 * 1024**2
_MAX_JSON = 4 * 1024**2
_MAX_THUMB = 1024**2
_CHUNK = 128 * 1024
_DIGEST = re.compile(r"[a-fA-F0-9]{64}")
_INVALID = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_DEVICE = re.compile(r"(?:CON(?:IN\$|OUT\$)?|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³])", re.I)


def _object(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("Duplicate field in pack JSON")
    return obj


def _json(data: bytes):
    obj = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object)
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object")
    return obj


def _relative(name: str) -> list[str]:
    if not isinstance(name, str) or len(name) > 2048:
        raise ValueError("Invalid pack path")
    parts = name.split("/")
    if len(parts) > 64 or any(
        not part
        or part.endswith((" ", "."))
        or _INVALID.search(part)
        or _DEVICE.fullmatch(part.split(".", 1)[0].rstrip(" "))
        for part in parts
    ):
        raise ValueError(f"Unsafe pack path: {name!r}")
    return parts


def _id(pack_id: str) -> None:
    if len(_relative(pack_id)) != 1 or pack_id.startswith("."):
        raise ValueError(f"Invalid pack ID: {pack_id!r}")


def _fold(name: str) -> str:
    return unicodedata.normalize("NFD", name).casefold()


def _url(url: str) -> None:
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username is not None or u.password is not None:
        raise ValueError(f"Expected an HTTPS pack URL: {url!r}")


class _HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _url(newurl)
        except ValueError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_pack(pack: PackInfo, local: bool):
    _id(pack.id)
    if pack.kind not in PACK_KINDS or not all(isinstance(s, str) and s for s in (pack.name, pack.version, pack.url)):
        raise ValueError("Invalid pack catalog entry")
    if type(pack.size) is not int or not 0 <= pack.size <= MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid pack archive size")
    if not isinstance(pack.sha256, str) or not (_DIGEST.fullmatch(pack.sha256) or (local and not pack.sha256)):
        raise ValueError("Invalid pack SHA-256")
    if not local and not pack.size:
        raise ValueError("Empty pack archive")
    if not isinstance(pack.meta, dict) or not isinstance(pack.thumbnail, str) or not isinstance(pack.preview, str):
        raise ValueError("Invalid pack catalog metadata")
    (_relative if local else _url)(pack.url)
    if pack.thumbnail:
        if local and not urlsplit(pack.thumbnail).scheme:
            _relative(pack.thumbnail)
        else:
            _url(pack.thumbnail)


def _catalog(doc: dict, local: bool) -> list[PackInfo]:
    if not isinstance(doc, dict) or doc.get("schema", 1) != 1 or not isinstance(doc.get("packs"), list):
        raise ValueError("Invalid pack catalog")
    try:
        packs = [PackInfo(**row) for row in doc["packs"]]
        for pack in packs:
            _check_pack(pack, local)
    except TypeError as exc:
        raise ValueError("Invalid pack catalog entry") from exc
    if len({_fold(p.id) for p in packs}) != len(packs):
        raise ValueError("Duplicate pack IDs")
    return packs


def _check_cancel(cancel) -> None:
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise CancelledError("Pack download cancelled")


def _read(stream, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Pack metadata or image exceeds its size limit")
    return data


def _kind(path: Path) -> int:
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return stat.S_IFLNK
    return stat.S_IFMT(info.st_mode)


def _dir(path: Path) -> Path:
    path.mkdir(exist_ok=True)
    if _kind(path) != stat.S_IFDIR:
        raise ValueError(f"Unsafe cache directory: {path}")
    return path


def _read_file(path: Path, limit: int) -> bytes:
    if _kind(path) != stat.S_IFREG:
        raise ValueError(f"Expected a regular file: {path}")
    with path.open("rb") as stream:
        return _read(stream, limit)


def _atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _proof_path(state: Path, digest: str, pack: PackInfo, folder: Path | None) -> Path:
    identity = json.dumps([pack.sha256.lower(), pack.size, str(folder) if folder else None]).encode()
    return state / f"{digest}-{hashlib.sha256(identity).hexdigest()}.verified"


def _exchange(stage: Path, dest: Path) -> bool:
    """Atomically swap two complete trees where the kernel and filesystem support it."""
    if sys.platform not in ("linux", "darwin"):
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    a, b = os.fsencode(stage), os.fsencode(dest)
    if sys.platform == "linux":
        swap = getattr(libc, "renameat2", None)
        args = (-100, a, -100, b, 2)  # AT_FDCWD and RENAME_EXCHANGE
    else:
        swap = getattr(libc, "renamex_np", None)
        args = (a, b, 2)  # RENAME_SWAP
    if swap is None:
        return False
    if swap(*args) == 0:
        return True
    error = ctypes.get_errno()
    if error in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP):
        return False
    raise OSError(error, os.strerror(error), dest)


def _promote(stage: Path, dest: Path, previous: Path) -> None:
    if dest.exists() or dest.is_symlink():
        if _kind(dest) != stat.S_IFDIR:
            raise ValueError("Expected a regular pack cache directory")
        if _exchange(stage, dest):
            return  # the old version is now inside the private work directory
        os.replace(dest, previous)
    try:
        os.replace(stage, dest)
    except OSError:
        if previous.exists():
            os.replace(previous, dest)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def _transfer(source, dest, limit: int, cancel, progress: Progress | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    while True:
        _check_cancel(cancel)
        block = source.read(min(_CHUNK, limit - count + 1))
        if not block:
            return count, digest.hexdigest()
        count += len(block)
        if count > limit:
            raise ValueError("File is larger than its declared size")
        digest.update(block)
        if dest is not None:
            dest.write(block)
        if progress is not None:
            progress(count, limit)


def _check_zip_directory(stream) -> None:
    # ZipFile constructs all ZipInfo objects before exposing any of them. Bound both the byte
    # allocation and the actual number of records, even when the footer understates the count.
    footer = zipfile._EndRecData(stream)
    if not footer:
        raise ValueError("Missing ZIP directory")
    size, count = footer[zipfile._ECD_SIZE], footer[zipfile._ECD_ENTRIES_TOTAL]
    if size > 12 * 1024**2 or count > MAX_MEMBERS:
        raise ValueError("Oversized ZIP directory")
    end = footer[zipfile._ECD_LOCATION]
    if footer[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
        stream.seek(end)
        if stream.read(4) == zipfile.stringEndArchive:
            # Older zipfile versions report the ordinary footer location for a ZIP64 archive.
            end -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
    if size > end:
        raise ValueError("Invalid ZIP directory offset")
    stream.seek(end - size)
    for _ in range(count):
        header = stream.read(zipfile.sizeCentralDir)
        if len(header) != zipfile.sizeCentralDir or not header.startswith(zipfile.stringCentralDir):
            raise ValueError("Invalid ZIP member header")
        # Variable-length filename, extra data and comment follow the fixed central directory header.
        stream.seek(sum(struct.unpack_from("<3H", header, 28)), os.SEEK_CUR)
    if stream.tell() != end:
        raise ValueError("ZIP member count differs from its directory")


def _entries(source: Path | ZipFile, cancel) -> Iterator[tuple[str, bool, int]]:
    if isinstance(source, ZipFile):
        for item in source.infolist():
            _check_cancel(cancel)
            is_dir = item.is_dir()
            mode = stat.S_IFMT(item.external_attr >> 16)
            if item.filename != item.orig_filename or mode not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG):
                raise ValueError(f"Unsupported ZIP entry: {item.orig_filename!r}")
            if item.flag_bits & 1 or item.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Unsupported ZIP entry: {item.orig_filename!r}")
            if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                # These ZipExtFile decoders bound each decompression by the requested read size.
                raise ValueError("Pack files must use stored or deflated ZIP compression")
            if is_dir and item.file_size:
                raise ValueError("A ZIP directory contains data")
            yield item.orig_filename.removesuffix("/") if is_dir else item.orig_filename, is_dir, item.file_size
        return

    if _kind(source) != stat.S_IFDIR:
        raise ValueError("Expected a regular pack directory")
    pending = [source]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                _check_cancel(cancel)
                path = Path(entry.path)
                mode = _kind(path)
                if mode not in (stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError(f"Unsupported pack file: {path.name!r}")
                is_dir = mode == stat.S_IFDIR
                if is_dir:
                    pending.append(path)
                yield path.relative_to(source).as_posix(), is_dir, entry.stat(follow_symlinks=False).st_size


def _contents(source, dest: Path | None, pack_id: str, pack: PackInfo | None, cancel) -> str:
    """Check the complete inventory and hashes, optionally copying into a private empty folder."""
    seen = set()
    directories = set()
    spellings = {}
    files = {}
    total = 0
    for name, is_dir, size in _entries(source, cancel):
        if name in seen or len(seen) >= MAX_MEMBERS:
            raise ValueError("Duplicate pack member or too many files")
        seen.add(name)
        parts = _relative(name)
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            if spellings.setdefault(_fold(prefix), prefix) != prefix:
                raise ValueError(f"Conflicting pack paths: {prefix!r}")
            if len(spellings) > MAX_MEMBERS:
                raise ValueError("Too many extracted pack entries")
            if i < len(parts) or is_dir:
                directories.add(prefix)
        if not is_dir:
            files[name] = size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("Expanded pack exceeds the size limit")

    if directories.intersection(files) or "manifest.json" not in files:
        raise ValueError("Invalid pack layout or missing manifest.json")

    def member(name):
        return source.open(name) if isinstance(source, ZipFile) else (source / name).open("rb")

    with member("manifest.json") as stream:
        raw = _read(stream, _MAX_JSON)
    manifest = _json(raw)
    if manifest.get("schema") != 1 or manifest.get("id") != pack_id or manifest.get("kind") not in PACK_KINDS:
        raise ValueError("Pack manifest identity mismatch")
    if pack is not None and (manifest["kind"], manifest.get("version")) != (pack.kind, pack.version):
        raise ValueError("Pack kind or version differs from the catalog")
    hashes = manifest.get("files")
    del files["manifest.json"]
    if not isinstance(hashes, dict) or hashes.keys() != files.keys():
        raise ValueError("Pack contents differ from the manifest file list")
    if dest:
        for name in directories:
            (dest / name).mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_bytes(raw)
    for name, size in files.items():
        if not isinstance(hashes[name], str) or not _DIGEST.fullmatch(hashes[name]):
            raise ValueError(f"Invalid SHA-256 for {name!r}")
        with member(name) as stream, (dest / name).open("xb") if dest else nullcontext() as out:
            actual, digest = _transfer(stream, out, size, cancel)
        if actual != size or digest != hashes[name].lower():
            raise ValueError(f"Pack integrity check failed for {name!r}")
    return hashlib.sha256(raw).hexdigest()


class GitHubPacks:
    """Release-backed PackService. A local checkout can supply folders or ZIPs for development."""

    def __init__(self, paths: Paths | Path, local_dir: Path | None = None):
        self.cache = paths.cache if isinstance(paths, Paths) else Path(paths)
        self.local_dir = Path(local_dir).resolve() if local_dir is not None else None
        tls = ssl.create_default_context(cafile=certifi.where())
        self._opener = build_opener(HTTPSHandler(context=tls), _HTTPSRedirects())

    def _local(self, name: str) -> Path:
        path = self.local_dir
        assert path is not None
        for part in _relative(name):
            path = path / part
            if _kind(path) == stat.S_IFLNK:
                raise ValueError(f"Symlink in local pack path: {name!r}")
        return path

    def _fetch(self, url: str, headers=None):
        _url(url)
        req = Request(url, headers={"User-Agent": "EDOPal/6", **(headers or {})})
        return self._opener.open(req, timeout=20)

    def catalog(self, kind: PackKind | None = None, *, refresh: bool = False) -> list[PackInfo]:
        if self.local_dir is not None:
            try:
                packs = _catalog(_json(_read_file(self._local("catalog.json"), _MAX_JSON)), True)
            except FileNotFoundError:
                packs = []
        else:
            self.cache.mkdir(parents=True, exist_ok=True)
            with file_lock(self.cache / ".pack-catalog.lock"):
                packs = self._release_catalog(refresh)
        return [p for p in packs if kind is None or p.kind == kind]

    def _release_catalog(self, refresh):
        # Cache the ETag and validated body together so a retry always revalidates the matching catalog.
        path = self.cache / "pack-catalog.json"
        etag, previous = "", None
        try:
            record = _json(_read_file(path, 6 * _MAX_JSON + 1024))
            previous = _catalog(record["catalog"], False)
            etag = record.get("etag", "")
            if not refresh and time.time() - path.stat().st_mtime < 86400:
                return previous
        except (KeyError, OSError, TypeError, ValueError):
            pass
        try:
            with self._fetch(CATALOG_URL, {"If-None-Match": etag} if etag else {}) as response:
                if getattr(response, "status", None) == 304 and previous is not None:
                    path.touch()
                    return previous
                doc = _json(_read(response, _MAX_JSON))
                packs = _catalog(doc, False)
                _atomic(path, json.dumps({"catalog": doc, "etag": response.headers.get("ETag", "")}).encode())
                return packs
        except HTTPError as exc:
            exc.close()
            if exc.code == 304 and previous is not None:
                path.touch()
            return previous or []
        except (OSError, HTTPException):
            return previous or []
        except ValueError:
            if previous is None:
                raise
            return previous

    @contextmanager
    def _locked(self, pack_id):
        _id(pack_id)
        self.cache.mkdir(parents=True, exist_ok=True)
        packs = _dir(self.cache / "packs")
        state = _dir(_dir(packs / ".state") / pack_id)
        dest, old = packs / pack_id, state / "previous"
        with file_lock(state / "lock"):
            # A stopped update may be between the two directory renames. Restore its last complete tree.
            if old.exists():
                if dest.exists():
                    shutil.rmtree(old)
                else:
                    os.replace(old, dest)
            work = state / "work"
            if work.exists():
                shutil.rmtree(work)
            yield dest, state

    def _verified(self, dest, state, pack, cancel) -> bool:
        try:
            if _kind(dest) != stat.S_IFDIR:
                return False
            manifest = _read_file(dest / "manifest.json", _MAX_JSON)
            digest = hashlib.sha256(manifest).hexdigest()
            if pack is None:
                if not any(state.glob(f"{digest}-*.verified")):
                    return False
            elif not _proof_path(state, digest, pack, None).is_file():
                if self.local_dir is None or not _proof_path(state, digest, pack, self.local_dir / pack.url).is_file():
                    return False
                # Local source folders may change in place during pack development.
                try:
                    local_manifest = _read_file(self._local(pack.url + "/manifest.json"), _MAX_JSON)
                except FileNotFoundError:
                    pass
                else:
                    if hashlib.sha256(local_manifest).hexdigest() != digest:
                        return False
            return _contents(dest, None, dest.name, pack, cancel) == digest
        except (OSError, TypeError, ValueError):
            return False

    def cached(self, pack_id: str) -> Path | None:
        with self._locked(pack_id) as (dest, state):
            return dest if self._verified(dest, state, None, None) else None

    def ensure(self, pack: PackInfo, progress: Progress | None = None, cancel=None) -> Path:
        _check_pack(pack, self.local_dir is not None)
        _check_cancel(cancel)
        with self._locked(pack.id) as (dest, state):
            _check_cancel(cancel)
            if self._verified(dest, state, pack, cancel):
                return dest
            work = _dir(state / "work")
            stage = _dir(work / "contents")
            try:
                source = self._local(pack.url) if self.local_dir else None
                folder = source if source and _kind(source) == stat.S_IFDIR else None
                if folder:
                    digest = _contents(source, stage, pack.id, pack, cancel)
                    if progress:
                        progress(pack.size, pack.size)
                else:
                    digest = self._unpack(pack, source, work, stage, progress, cancel)
                _check_cancel(cancel)
                stamp = _proof_path(state, digest, pack, folder)
                _atomic(stamp, b"")
                _promote(stage, dest, state / "previous")
                for proof in state.glob("*.verified"):
                    if proof != stamp:
                        proof.unlink()
                return dest
            finally:
                shutil.rmtree(work)

    def _unpack(self, pack, source, work, stage, progress, cancel):
        if not pack.size or not _DIGEST.fullmatch(pack.sha256):
            raise ValueError("A pack ZIP needs an archive size and SHA-256")
        if source is not None and _kind(source) != stat.S_IFREG:
            raise ValueError("Expected a regular pack ZIP")
        archive = work / "download.zip"
        with source.open("rb") if source else self._fetch(pack.url) as stream, archive.open("xb") as out:
            size, checksum = _transfer(stream, out, pack.size, cancel, progress)
        if size != pack.size or checksum != pack.sha256.lower():
            raise ValueError("Pack archive size or SHA-256 mismatch")
        try:
            with archive.open("rb") as stream:
                _check_zip_directory(stream)
                with ZipFile(stream) as z:
                    return _contents(z, stage, pack.id, pack, cancel)
        except (BadZipFile, NotImplementedError) as exc:
            raise ValueError("Invalid or unsupported pack ZIP") from exc

    def thumbnail(self, pack: PackInfo) -> Path | None:
        _check_pack(pack, self.local_dir is not None)
        if not pack.thumbnail:
            return None
        key = hashlib.sha256(json.dumps([pack.id, pack.version, pack.sha256, pack.thumbnail]).encode()).hexdigest()
        self.cache.mkdir(parents=True, exist_ok=True)
        suffix = Path(urlsplit(pack.thumbnail).path).suffix.lower()
        # The music page uses this same small-media cache for its catalog preview clip.
        media_suffix = suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".ogg", ".mp3", ".flac", ".wav"} else ".img"
        path = _dir(self.cache / "thumbnails") / (key + media_suffix)
        try:
            if path.exists() and _kind(path) == stat.S_IFREG:
                return path
            if self.local_dir is not None and not urlsplit(pack.thumbnail).scheme:
                data = _read_file(self._local(pack.thumbnail), _MAX_THUMB)
            else:
                with self._fetch(pack.thumbnail) as stream:
                    data = _read(stream, _MAX_THUMB)
            if not data:
                return None
            _atomic(path, data)
            return path
        except (OSError, HTTPException):
            return None

    def remove(self, pack_id: str) -> None:
        with self._locked(pack_id) as (dest, state):
            if dest.exists() or dest.is_symlink():
                if _kind(dest) != stat.S_IFDIR:
                    raise ValueError("Expected a regular pack cache directory")
                shutil.rmtree(dest)
            for proof in state.glob("*.verified"):
                proof.unlink()

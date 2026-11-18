"""Editable JSON documents and content-addressed local PNG/JPEG originals."""

import hashlib
import json
import stat
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from PySide6.QtGui import QImage

import config
from content.cache import file_sha256
from content.manifest import SHA256
from content.media import Changes, Item, Media, MediaCollection, Origin
from extras import _atomic_write, _regular_path
from state_lock import locked

from .imaging import MAX_IMAGE_BYTES, decode_photo
from .model import MAX_DEFINITION, Theme, check_id


def _read(path: Path, limit: int) -> bytes:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"Missing or oversized file: {path.name}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Oversized file: {path.name}")
    return data


@dataclass(frozen=True)
class ThemeIssue:
    filename: str
    reason: str
    stamp: tuple[int, int, int] | None


class ThemeLibrary:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory or Path(config.user_data_dir()) / "themes").absolute()

    def _path(self, name: str) -> Path:
        return _regular_path(self.directory, name)

    def entries(
        self, issues: list[ThemeIssue] | None = None, *, reserved: Iterable[str] = ()
    ) -> dict[str, Theme]:
        return self._entries("", issues, set(reserved))

    def _entries(
        self, subdir: str, issues: list[ThemeIssue] | None, reserved: set[str]
    ) -> dict[str, Theme]:
        entries = {}
        folder = self._path(str(Path(subdir) / ".library.lock")).parent
        for path in sorted(folder.glob("*.json")):
            name = path.relative_to(self.directory).as_posix()
            stamp = None
            try:
                path = self._path(name)
                info = path.stat()
                stamp = info.st_ino, info.st_mtime_ns, info.st_size
                uid = check_id(path.stem)
                if uid in reserved or uid in entries:
                    raise ValueError("Another theme already uses this ID.")
                entries[uid] = Theme.from_json(_read(path, MAX_DEFINITION))
            except (OSError, ValueError, KeyError) as exc:
                if issues is not None:
                    issues.append(ThemeIssue(name, str(exc), stamp))
        return entries

    def set_aside(self, issue: ThemeIssue) -> None:
        source = self._path(issue.filename)
        info = source.stat()
        if (info.st_ino, info.st_mtime_ns, info.st_size) != issue.stamp:
            raise ValueError("The theme file changed. Refresh the collection and try again.")
        target = self._path(f"quarantine/{uuid4().hex}-{source.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def load(self, theme_id: str) -> Theme:
        return Theme.from_json(_read(self._path(check_id(theme_id) + ".json"), MAX_DEFINITION))

    def delete(self, theme_id: str, *, expected: Theme | None = None) -> None:
        """Remove from the library, keeping its recipe for games that still use it."""
        name = check_id(theme_id) + ".json"
        removed = self._path("removed/" + name)
        removed.parent.mkdir(parents=True, exist_ok=True)
        with locked(self._path(".library.lock").parent, ".library.lock"):
            if expected is not None and self.load(theme_id) != expected:
                raise ValueError(
                    "This theme changed in another window. Refresh before removing it."
                )
            self._path(name).replace(removed)

    def removed_entries(
        self, issues: list[ThemeIssue] | None = None, *, reserved: Iterable[str] = ()
    ) -> dict[str, Theme]:
        """Deleted recipes can still identify installation history in another game."""
        return self._entries("removed", issues, set(reserved))

    def save(
        self,
        theme: Theme,
        theme_id: str | None = None,
        *,
        expected: Theme | None = None,
        require_absent: bool = False,
    ) -> str:
        theme_id = uuid4().hex if theme_id is None else check_id(theme_id)
        if theme.image:
            data = self._photo(theme)
            if not self._path("images/" + theme.image).exists():
                # A saved theme owns its wallpaper independently of the downloadable pack.
                self._store_photo(data)
        with locked(self._path(".library.lock").parent, ".library.lock"):
            path = self._path(theme_id + ".json")
            if expected is not None and (not path.exists() or self.load(theme_id) != expected):
                raise ValueError(
                    "This theme changed in another window. Save your draft as a new theme."
                )
            if require_absent and path.exists():
                raise ValueError("Another theme already uses this ID. Its saved copy was kept.")
            _atomic_write(path, theme.to_json())
        return theme_id

    def _collection(self) -> MediaCollection:
        def personal_path(name: str) -> Path:
            if not SHA256.fullmatch(name):
                raise ValueError("Invalid library wallpaper reference.")
            return self._path("images/" + name)

        return MediaCollection(self.directory / "images/packs", personal_path)

    def wallpapers(self) -> tuple[Media, ...]:
        return self._collection().entries()

    def replace_wallpapers(self, origin: Origin, sources: Iterable[tuple[Item, Path]]) -> Changes:
        sources = list(sources)
        personal = {}
        for _, source in sources:
            key = file_sha256(source)
            path = self._path("images/" + key)
            if path.is_file():
                personal[key] = path
        return self._collection().replace(origin, sources, personal=personal)

    def add_image(self, path: Path) -> str:
        data = _read(path, MAX_IMAGE_BYTES)
        decode_photo(data)
        return self._store_photo(data)

    def _store_photo(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        _atomic_write(self._path("images/" + digest), data)
        return digest

    def _photo(self, theme: Theme) -> bytes:
        path = self._path("images/" + str(theme.image))
        if not path.exists():
            path = next((row.path for row in self.wallpapers() if row.digest == theme.image), path)
        data = _read(path, MAX_IMAGE_BYTES)
        if hashlib.sha256(data).hexdigest() != theme.image:
            raise ValueError("The saved wallpaper has changed.")
        return data

    def image(self, theme: Theme) -> QImage | None:
        return decode_photo(self._photo(theme)) if theme.image else None

    def export_file(self, theme_id: str, path: Path) -> None:
        theme = self.load(theme_id)
        if not theme.image:
            _atomic_write(path, theme.to_json())
            return
        with NamedTemporaryFile(dir=path.parent, delete=False) as file:
            staged = Path(file.name)
        try:
            with ZipFile(staged, "w", compression=ZIP_STORED) as archive:
                archive.writestr("theme.json", theme.to_json())
                archive.writestr("wallpaper", self._photo(theme))
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)

    def import_file(self, path: Path) -> str:
        """Import as a fresh ID. Never extract filenames or follow JSON paths."""
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES + 8192:
            raise ValueError("Invalid theme archive size.")
        with path.open("rb") as file:
            if file.read(4) != b"PK\x03\x04":
                theme = Theme.from_json(_read(path, MAX_DEFINITION))
                if theme.image:
                    raise ValueError("A shared image theme must include its wallpaper.")
                return self.save(theme)
            if file.seek(0, 2) < 22:
                raise ValueError("Incomplete theme archive.")
            # Bound the central directory before ZipFile allocates ZipInfo objects.
            file.seek(-22, 2)
            sig, disk, start, count, total, length, offset, comment = struct.unpack(
                "<4s4H2IH", file.read(22)
            )
            if (
                sig != b"PK\x05\x06"
                or disk
                or start
                or count != total
                or total not in (1, 2)
                or not 46 <= length <= 1024
                or offset + length + 22 != file.tell()
                or comment
            ):
                raise ValueError("Invalid theme archive directory.")
            # ZipFile otherwise prefers an optional ZIP64 record over the bounded
            # 32-bit directory that was just checked. Our shares never need ZIP64.
            file.seek(-42, 2)
            if file.read(4) == b"PK\x06\x07":
                raise ValueError("ZIP64 theme archives are unsupported.")
            try:
                with ZipFile(file) as archive:
                    members = archive.infolist()
                    if (
                        [m.filename for m in members].count("theme.json") != 1
                        or len(members) != total
                        or any(
                            m.orig_filename not in ("theme.json", "wallpaper")
                            or m.flag_bits & 1
                            or m.compress_type != ZIP_STORED
                            or m.compress_size != m.file_size
                            or stat.S_ISLNK(m.external_attr >> 16)
                            or m.file_size
                            > (MAX_DEFINITION if m.filename == "theme.json" else MAX_IMAGE_BYTES)
                            for m in members
                        )
                    ):
                        raise ValueError("Unexpected or oversized theme archive member.")
                    theme = Theme.from_json(archive.read("theme.json"))
                    if bool(theme.image) != ("wallpaper" in archive.namelist()):
                        raise ValueError("Wallpaper and theme definition do not match.")
                    if theme.image:
                        data = archive.read("wallpaper")
                        if hashlib.sha256(data).hexdigest() != theme.image:
                            raise ValueError("Wallpaper checksum mismatch.")
                        decode_photo(data)
                        self._store_photo(data)
            except (BadZipFile, EOFError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Damaged theme archive.") from exc
        return self.save(theme)

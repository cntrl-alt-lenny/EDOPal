"""Editable JSON documents and content-addressed local PNG/JPEG originals."""

import hashlib
import json
import stat
import struct
from collections.abc import Iterable
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


class ThemeLibrary:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (directory or Path(config.user_data_dir()) / "themes").absolute()

    def _path(self, name: str) -> Path:
        return _regular_path(self.directory, name)

    def entries(self) -> dict[str, Theme]:
        return {path.stem: self.load(path.stem) for path in sorted(self.directory.glob("*.json"))}

    def load(self, theme_id: str) -> Theme:
        return Theme.from_json(_read(self._path(check_id(theme_id) + ".json"), MAX_DEFINITION))

    def save(self, theme: Theme, theme_id: str | None = None) -> str:
        theme_id = uuid4().hex if theme_id is None else check_id(theme_id)
        if theme.image:
            data = self._photo(theme)
            if not self._path("images/" + theme.image).exists():
                # A saved theme owns its wallpaper independently of the downloadable pack.
                self._store_photo(data)
        _atomic_write(self._path(theme_id + ".json"), theme.to_json())
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

"""Validation and atomic storage of personal audio."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from music.catalogue import SCENES, catalog, library_root
from music.storage import copy_synced, ensure_directory, sync_directory
from state_lock import locked

FORMATS = frozenset({".flac", ".ogg", ".mp3", ".wav"})


def _scene(scene: str) -> str:
    if scene not in SCENES:
        raise ValueError("Choose a valid background music scene.")
    return scene


def _audio(folder: Path) -> Iterator[Path]:
    if folder.is_dir():
        for item in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if item.is_file() and item.suffix.lower() in FORMATS:
                yield item


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_audio(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() not in FORMATS:
        raise ValueError("Choose a FLAC, Ogg Vorbis, MP3 or WAV audio file.")
    with path.open("rb") as source:
        header = source.read(12)
    extension = path.suffix.lower()
    headers = {".flac": b"fLaC", ".ogg": b"OggS", ".wav": (b"RIFF", b"RF64")}
    if extension in headers and not header.startswith(headers[extension]):
        raise ValueError(f"{path.name} does not contain {extension[1:].upper()} audio.")
    if extension == ".mp3" and not (
        header.startswith(b"ID3")
        or (len(header) > 1 and header[0] == 255 and header[1] & 224 == 224)
    ):
        raise ValueError(f"{path.name} does not appear to contain MP3 audio.")
    try:
        import miniaudio
    except ImportError:
        return
    try:
        info = miniaudio.get_file_info(str(path))
    except miniaudio.MiniaudioError as exc:
        raise ValueError(
            f"Cannot decode {path.name}. Choose FLAC, Ogg Vorbis, MP3 or WAV audio."
        ) from exc
    if not info.num_frames or not info.sample_rate:
        raise ValueError(f"{path.name} has no playable audio.")


def _copy_new(source: Path, destination: Path, digest: str) -> None:
    """Publish a complete file without replacing an existing one."""
    ensure_directory(destination.parent)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=".import-", delete=False
    ) as staged:
        temp = Path(staged.name)
    try:
        copy_synced(source, temp)
        if _digest(temp) != digest:
            raise OSError("The source changed during copying. Please try again.")
        if destination.exists():
            raise FileExistsError(destination)
        # Both paths are in the locked library/installation on the same volume.
        os.rename(temp, destination)
        sync_directory(destination.parent)
    finally:
        temp.unlink(missing_ok=True)


def import_track(source: Path, key: str, scene: str, *, root: Path | None = None) -> Path:
    return store_track(source, key, scene, root=root)[0]


def store_track(
    source: Path, key: str, scene: str, *, root: Path | None = None, expected_digest: str = ""
) -> tuple[Path, bool]:
    """Store one recording and report whether a new file was added."""
    root = root or library_root()
    return _store_track(
        source,
        key,
        scene,
        root=root,
        expected_digest=expected_digest,
        guard=locked(root, ".import.lock"),
    )


def _store_track(
    source: Path,
    key: str,
    scene: str,
    *,
    root: Path,
    expected_digest: str,
    guard: AbstractContextManager[None] | None = None,
) -> tuple[Path, bool]:
    """Batch import already owns the library lock. A standalone import acquires it after validation."""
    if key not in catalog(root=root):
        raise ValueError("Choose a game from the library.")
    validate_audio(source)
    storage = root.resolve()
    base = storage / key / _scene(scene)
    if not base.resolve().is_relative_to(storage):
        raise OSError("The target folder points outside your soundtrack library.")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source.stem).strip().rstrip(".")[:160] or "Track"
    if re.fullmatch(r"(?:AUX|PRN|CON|NUL|COM[1-9]|LPT[1-9])", stem, re.I):
        stem = "_" + stem
    digest = _digest(source)
    if expected_digest and digest != expected_digest:
        raise ValueError("The file changed after planning. Scan it again.")
    size = source.stat().st_size
    from music.packs import pack_media

    with guard or nullcontext(), locked(base):
        for row in pack_media(storage):
            if (
                row.item.game == key
                and scene in row.item.scenes
                and row.digest == digest
                and row.path.is_file()
                and _digest(row.path) == digest
            ):
                return row.path, False
        for existing in _audio(base):
            if existing.stat().st_size == size and _digest(existing) == digest:
                return existing, False
        out = base / (stem + source.suffix.lower())
        index = 2
        while out.exists():
            out = base / f"{stem} ({index}){source.suffix.lower()}"
            index += 1
        _copy_new(source, out, digest)
        return out, True

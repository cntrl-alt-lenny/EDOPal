"""Import a portable soundtrack collection or music from a local Power of Chaos game."""

from __future__ import annotations

import struct
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from music.catalogue import library_root, personal_games, save_personal_game
from music.importing import ImportItem, ImportResult, execute_import, plan_import
from music_library import FORMATS, SCENES, catalog, store_track

__all__ = [
    "ImportItem",
    "ImportResult",
    "execute_import",
    "plan_import",
    "import_library",
    "export_library",
    "import_power_of_chaos",
]

Progress = Callable[[int, int], None]
Created = Callable[[Path], None]

# Entries in the three original Voice.dat indices. Effect and dialogue records are skipped.
_GAMES = {
    "yugi": (
        "pocyugi",
        {
            "kaban.wav": ("deck", "Card trunk"),
            "stage1.wav": ("duel", "Duel 1"),
            "stage2.wav": ("duel", "Duel 2"),
        },
    ),
    "kaiba": (
        "pockaiba",
        {
            "m_kaban.wav": ("deck", "Card trunk"),
            "m_duel1.wav": ("duel", "Duel 1"),
            "m_duel2.wav": ("duel", "Duel 2"),
        },
    ),
    "joey": (
        "pocjoey",
        {
            "m_kaban.wav": ("deck", "Card trunk"),
            "m_duel1.wav": ("duel", "Duel 1"),
            "m_duel2.wav": ("duel", "Duel 2"),
            "m_duel3.wav": ("duel", "Duel 3"),
        },
    ),
}
_NIBBLES = bytes((i >> 4 | i << 4) & 255 for i in range(256))


def import_library(
    folder: Path,
    *,
    root: Path | None = None,
    progress: Progress | None = None,
    created: Created | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Import a local game/scene collection; skip hidden, unsupported and unrelated files."""
    folder = folder.resolve(strict=True)
    files = []
    destination = root or library_root()
    custom = personal_games(folder)
    for game in custom.values():
        if checkpoint:
            checkpoint()
        current = destination / game.id / "game.json"
        if not current.exists():
            save_personal_game(game, destination)
            if created:
                created(current)
    for key in catalog(root=destination):
        if checkpoint:
            checkpoint()
        for scene in SCENES:
            base = folder / key / scene
            if not base.is_dir():
                continue
            for audio in sorted(base.iterdir()):
                if (
                    audio.suffix.lower() in FORMATS
                    and audio.is_file()
                    and not audio.name.startswith(".")
                ):
                    if not audio.resolve().is_relative_to(folder):
                        raise ValueError(f"{audio.name} is a link outside the selected collection.")
                    files.append((audio, key, scene))
    if not files and custom:
        return 0, 0
    if not files:
        raise ValueError(
            "Choose a soundtracks folder copied from another installation. Each recording belongs in a game-id/scene folder."
        )
    return _store(files, root, progress, created)


def export_library(
    destination: Path,
    *,
    root: Path | None = None,
    progress: Progress | None = None,
    created: Created | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Copy the portable game manifests and recordings into a transfer folder."""
    source = (root or library_root()).resolve()
    if destination.resolve().is_relative_to(source):
        raise ValueError("Choose an export folder outside the source library.")
    return import_library(
        source, root=destination, progress=progress, created=created, checkpoint=checkpoint
    )


def _store(
    files: list[tuple[Path, str, str]],
    root: Path | None,
    progress: Progress | None,
    created: Created | None = None,
) -> tuple[int, int]:
    added = duplicates = 0
    failures = []
    for i, (path, key, scene) in enumerate(files, 1):
        try:
            stored, new = store_track(path, key, scene, root=root)
            if new and created:
                created(stored)
            added += int(new)
            duplicates += int(not new)
        except Exception as exc:
            failures.append(f"{path.name} ({exc})")
        if progress:
            progress(i, len(files))
    if failures:
        detail = ", ".join(failures)
        raise ValueError(
            f"Imported {added}, already present {duplicates}, failed {len(failures)}. {detail}"
        )
    return added, duplicates


def _read_game(
    stream: BinaryIO, length: int, temporary: Path, checkpoint: Callable[[], None] | None = None
) -> list[tuple[Path, str, str]]:
    header = stream.read(12)
    if len(header) != 12 or header[:8] != b"KCEJYUGI":
        return []
    count = struct.unpack_from("<I", header, 8)[0]
    index_size = count * 268
    if not 0 < count <= 8192 or index_size + 12 > length:
        raise ValueError("The game audio index is incomplete.")
    table = stream.read(index_size)
    if len(table) != index_size:
        raise ValueError("The game audio index is incomplete.")
    recordings = []
    for offset in range(0, index_size, 268):
        name = (
            table[offset : offset + 256]
            .split(b"\0", 1)[0]
            .translate(_NIBBLES)
            .decode("ascii", "ignore")
        )
        parts = name.lower().replace("\\", "/").split("/")
        if len(parts) != 4 or parts[2] != "stream" or parts[0] not in _GAMES:
            continue
        key, wanted = _GAMES[parts[0]]
        if parts[-1] not in wanted:
            continue
        scene, title = wanted[parts[-1]]
        start, size = struct.unpack_from("<II", table, offset + 256)
        if start < index_size + 12 or not 0 < size <= 128 * 1024 * 1024 or start + size > length:
            raise ValueError("The game soundtrack index points to an incomplete track.")
        recordings.append((start, size, key, scene, title))
    result = []
    for start, size, key, scene, title in sorted(recordings):
        destination = temporary / key / scene / (title + ".wav")
        # Separate temporary directories let the content-aware store compare alternate takes.
        if destination.exists():
            destination = Path(tempfile.mkdtemp(dir=temporary)) / destination.name
        stream.seek(start)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as out:
            remaining = size
            while remaining:
                if checkpoint:
                    checkpoint()
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("The game soundtrack ends unexpectedly.")
                out.write(chunk)
                remaining -= len(chunk)
        result.append((destination, key, scene))
    return result


def import_power_of_chaos(
    source: Path,
    *,
    root: Path | None = None,
    progress: Progress | None = None,
    created: Created | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Read original PCM WAV recordings. A trilogy ZIP is opened without unpacking the games."""
    with tempfile.TemporaryDirectory(prefix="edopro-game-music-") as tmp:
        temporary = Path(tmp)
        files = []
        if source.suffix.lower() == ".zip":
            try:
                with ZipFile(source) as archive:
                    banks = [
                        i
                        for i in archive.infolist()
                        if PurePosixPath(i.filename.replace("\\", "/")).name.lower() == "voice.dat"
                    ]
                    if len(banks) > 12:
                        raise ValueError("Choose the original game archive or its Voice.dat file.")
                    for bank in banks:
                        if bank.file_size > 1024**3:
                            raise ValueError("Choose an original Power of Chaos Voice.dat archive.")
                        with archive.open(bank) as stream:
                            files.extend(_read_game(stream, bank.file_size, temporary, checkpoint))
            except (BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ValueError(
                    "The ZIP could not be read. Try the original Voice.dat file."
                ) from exc
        else:
            with source.open("rb") as stream:
                files = _read_game(stream, source.stat().st_size, temporary, checkpoint)
        if not files:
            raise ValueError(
                "Choose Voice.dat from Yugi the Destiny, Kaiba the Revenge or Joey the Passion, or a ZIP containing one of those games."
            )
        return _store(files, root, progress, created)

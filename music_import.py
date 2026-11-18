"""Import a portable soundtrack collection or music from a local Power of Chaos game."""

from __future__ import annotations

import struct
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from music_library import FORMATS, SCENES, catalog, store_track

Progress = Callable[[int, int], None]

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
    folder: Path, *, root: Path | None = None, progress: Progress | None = None
) -> tuple[int, int]:
    """Import a local game/scene collection; skip hidden, unsupported and unrelated files."""
    folder = folder.resolve(strict=True)
    files = []
    for key in catalog():
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
    if not files:
        raise ValueError(
            "Choose a soundtracks folder copied from another installation. It contains game folders with deck or duel audio inside."
        )
    return _store(files, root, progress)


def _store(files: list[tuple[Path, str, str]], root: Path | None, progress: Progress | None):
    added = 0
    for i, (path, key, scene) in enumerate(files):
        try:
            _, new = store_track(path, key, scene, root=root)
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"Imported {i} of {len(files)} recordings. {exc} You can retry safely."
            ) from exc
        added += int(new)
        if progress:
            progress(i + 1, len(files))
    return added, len(files) - added


def _read_game(stream: BinaryIO, length: int, temporary: Path):
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
        if destination.exists():
            continue
        stream.seek(start)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as out:
            remaining = size
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("The game soundtrack ends unexpectedly.")
                out.write(chunk)
                remaining -= len(chunk)
        result.append((destination, key, scene))
    return result


def import_power_of_chaos(
    source: Path, *, root: Path | None = None, progress: Progress | None = None
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
                            files.extend(_read_game(stream, bank.file_size, temporary))
            except (BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ValueError(
                    "The ZIP could not be read. Try the original Voice.dat file."
                ) from exc
        else:
            with source.open("rb") as stream:
                files = _read_game(stream, source.stat().st_size, temporary)
        if not files:
            raise ValueError(
                "Choose Voice.dat from Yugi the Destiny, Kaiba the Revenge or Joey the Passion, or a ZIP containing one of those games."
            )
        return _store(files, root, progress)

"""Move, roll back and retire files belonging to a playlist transaction."""

import contextlib
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

from music.audio import FORMATS, _audio, _copy_new, _digest
from music.discovery import Track, _sound_path, describe
from music.storage import ensure_directory, sync_directory


def _move(source: Path, destination: Path, changes: list[tuple[Path, Path]]) -> None:
    if destination.exists():
        raise FileExistsError(
            f"Two tracks share the name {destination.name}. Rename one before changing this playlist."
        )
    ensure_directory(destination.parent)
    source.rename(destination)
    changes.append((source, destination))
    sync_directory(destination.parent)
    sync_directory(source.parent)


def _rollback(changes: list[tuple[Path, Path]]) -> None:
    for original, moved in reversed(changes):
        if original.exists():
            raise FileExistsError(
                f"Another file now occupies {original}. Keep the recovery record."
            )
        moved.rename(original)
        sync_directory(original.parent)
        sync_directory(moved.parent)


def _retire_current(folder: Path, state: Path, record: dict, changes: list) -> None:
    # Only move the exact unchanged file that this tool installed. A replaced file is user data.
    if not record or Path(record["name"]).name != record["name"]:
        return
    active = folder / record["name"]
    if active.is_file() and _digest(active) == record["digest"]:
        fd, name = tempfile.mkstemp(dir=state, prefix=".retiring-")
        os.close(fd)
        retired = Path(name)
        retired.unlink()
        _move(active, retired, changes)


def _clear_staging(state: Path) -> None:
    """After commit, reclaim transient copies including those from an interrupted change."""
    for path in state.iterdir():
        if not re.fullmatch(r"\.(?:prepared|retiring)-[a-z0-9_]{8}", path.name):
            continue
        with contextlib.suppress(OSError):
            if path.is_file():
                path.unlink()
            elif path.resolve() == path:
                for staged in path.iterdir():
                    if (
                        re.fullmatch(r"HD Sync - [a-z0-9]+ - [a-f0-9]{16}", staged.stem)
                        and staged.suffix in FORMATS
                    ):
                        staged.unlink()
                path.rmdir()


def _history(game: Path, scene: str) -> Path:
    return _sound_path(game, ".edopro-hd-sync", scene, "undo")


def _remember(
    game: Path, scene: str, rows: Iterable[Track], checkpoint: Callable[[], None] | None = None
) -> list[dict]:
    history = _history(game, scene)
    result = []
    for row in rows:
        if checkpoint:
            checkpoint()
        digest = _digest(row.path)
        record = describe(row, digest)
        copy = history / record["name"]
        if not copy.exists():
            _copy_new(row.path, copy, digest)
        if _digest(copy) != digest:
            raise OSError("The previous playlist changed during copying.")
        result.append(record)
    return result


def _prune_history(game: Path, scene: str, previous: list[dict]) -> None:
    keep = {row["name"] for row in previous}
    for path in _audio(_history(game, scene)):
        if path.name not in keep:
            with contextlib.suppress(OSError):
                path.unlink()

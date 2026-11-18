"""Read the replay shelf and explicitly preserve or share selected files."""

import hashlib
import json
import math
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

from .annotations import AnnotationStore, ReplayNotes
from .format import (
    MAX_FILE_BYTES,
    ReplayDetails,
    ReplayFormatError,
    ReplayHeader,
    parse_header,
    parse_replay,
)

_EXTENSIONS = {".yrp", ".yrpx"}
ReplaySort = Literal["date", "name", "turns"]


@dataclass(frozen=True)
class ReplayEntry:
    path: Path
    is_last: bool
    replay_id: str | None
    header: ReplayHeader | None
    details: ReplayDetails | None
    annotation: ReplayNotes
    error: str | None = None


def _folder(installation: str | Path) -> Path:
    root = Path(installation).resolve()
    folder = root / "replay"
    if folder.resolve() != folder:
        raise ValueError("The replay folder redirects to another location.")
    return folder


def _is_last(path: Path) -> bool:
    return path.stem.casefold() == "_lastreplay"


def _files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.suffix.casefold() in _EXTENSIONS and not path.is_symlink() and path.is_file()
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def find_last_replays(installation: str | Path) -> tuple[Path, ...]:
    """Find the rolling streamed and legacy replay files, if present."""
    return tuple(path for path in _files(_folder(installation)) if _is_last(path))


def _signature(info: os.stat_result) -> tuple[int, ...]:
    # Python 3.12's Windows fstat can report mtime in the ctime slot.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _snapshot(path: Path) -> tuple[tuple[int, ...], bytes]:
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError("Select a regular replay file.")
    if initial.st_size > MAX_FILE_BYTES:
        raise ReplayFormatError("The replay exceeds the file size limit.")
    signature = _signature(initial)
    with path.open("rb") as stream:
        if _signature(os.fstat(stream.fileno())) != signature:
            raise OSError("The replay changed while it was being opened.")
        data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) != initial.st_size or _signature(os.fstat(stream.fileno())) != signature:
            raise OSError("The replay changed while it was being read.")
    if _signature(path.lstat()) != signature:
        raise OSError("The replay changed while it was being read.")
    return signature, data


def index_replays(
    installation: str | Path,
    *,
    annotations: AnnotationStore | None = None,
) -> tuple[ReplayEntry, ...]:
    """Read immediate replay files. Keep entries with unreadable or unsupported metadata."""
    notes = annotations.all() if annotations else {}
    entries = []
    for path in _files(_folder(installation)):
        replay_id = None
        header = None
        try:
            _, data = _snapshot(path)
            replay_id = hashlib.sha256(data).hexdigest()
            header = parse_header(data)
            details = parse_replay(data)
            error = None
        except (OSError, ValueError) as exc:
            details = None
            error = str(exc) if isinstance(exc, ValueError) else "The replay could not be read."
        annotation = notes.get(replay_id, ReplayNotes()) if replay_id else ReplayNotes()
        entries.append(
            ReplayEntry(path, _is_last(path), replay_id, header, details, annotation, error)
        )
    return tuple(entries)


def search_replays(
    entries: tuple[ReplayEntry, ...],
    query: str = "",
    *,
    sort: ReplaySort = "date",
    descending: bool | None = None,
) -> tuple[ReplayEntry, ...]:
    """Match every search word across names, labels, notes, tags and player names."""
    if sort not in {"date", "name", "turns"}:
        raise ValueError("Unknown replay sort.")
    if descending is None:
        descending = sort != "name"
    terms = query.casefold().split()
    ranked: list[tuple[int | str, ReplayEntry]] = []
    unknown = []
    for entry in sorted(entries, key=lambda item: (item.path.name.casefold(), item.path.name)):
        annotation = entry.annotation
        players = [name for team in entry.details.teams for name in team] if entry.details else []
        values = (entry.path.name, annotation.label, annotation.notes, *annotation.tags, *players)
        text = "\n".join(values).casefold()
        if not all(term in text for term in terms):
            continue
        if sort == "name":
            value = (annotation.label or entry.path.stem).casefold()
        elif sort == "turns":
            value = entry.details.turns if entry.details else None
        else:
            value = (entry.header.timestamp or None) if entry.header else None
        if value is None:
            unknown.append(entry)
        else:
            ranked.append((value, entry))
    ranked.sort(key=lambda pair: pair[0], reverse=descending)
    return tuple(entry for _, entry in ranked) + tuple(unknown)


def _publish(staged: Path, destination: Path) -> None:
    # Unix rename can replace a target. Linking publishes a complete file exclusively.
    if os.name == "nt":
        staged.rename(destination)
    else:
        os.link(staged, destination)


def preserve_last_replay(
    installation: str | Path,
    *,
    source: Path | None = None,
    settle_seconds: float = 1.0,
) -> Path | None:
    """Archive a rolling replay after two matching snapshots. Call from a worker.

    Without an explicit source this selects the most recently modified rolling file.
    Returns None if no replay is ready. Existing identical archives are reused.
    """
    if not math.isfinite(settle_seconds) or settle_seconds <= 0:
        raise ValueError("The settling interval must be positive.")
    rolling = find_last_replays(installation)
    if source is not None:
        source = Path(source).absolute()
        if source not in rolling:
            raise ValueError("Select a rolling replay from this installation.")
    try:
        if source is None:
            source = max(
                rolling,
                key=lambda path: (path.stat().st_mtime_ns, path.suffix.lower()),
                default=None,
            )
        if source is None:
            return None
        before = _snapshot(source)
        sleep(settle_seconds)
        after = _snapshot(source)
        if before != after:
            return None
        details = parse_replay(after[1])
        if not details.header.compressed and details.header.format == "yrp1":
            from music_library import _game_running

            # Single-player recordings can pause indefinitely between response writes.
            if _game_running(Path(installation)):
                return None
        if _signature(source.lstat()) != after[0]:
            return None
    except (OSError, ReplayFormatError):
        return None
    data = after[1]
    digest = hashlib.sha256(data).hexdigest()
    date = details.header.date
    stamp = date.strftime("%Y%m%d-%H%M%S") if date else "undated"
    suffix = ".yrpX" if details.header.format == "yrpX" else ".yrp"
    with NamedTemporaryFile(dir=source.parent, prefix=".replay-", delete=False) as temporary:
        staged = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.close()
            for number in range(10000):
                ending = f"-{number}" if number else ""
                destination = source.with_name(f"Replay-{stamp}-{digest[:16]}{ending}{suffix}")
                try:
                    _publish(staged, destination)
                    return destination
                except FileExistsError:
                    if not destination.is_symlink() and destination.is_file():
                        try:
                            if _snapshot(destination)[1] == data:
                                return destination
                        except (OSError, ValueError):
                            continue
            raise FileExistsError("No unused replay archive name is available.")
        finally:
            temporary.close()
            staged.unlink(missing_ok=True)


def export_replay(entry: ReplayEntry, destination: str | Path) -> Path:
    """Create a ZIP containing unchanged replay bytes and the current shelf annotation."""
    destination = Path(destination)
    _, data = _snapshot(entry.path)
    if entry.replay_id != hashlib.sha256(data).hexdigest():
        raise ValueError("The replay has changed. Refresh the shelf before exporting.")
    # Fixed archive member names avoid disclosing local paths and custom filenames.
    suffix = ".yrpX" if entry.path.suffix.casefold() == ".yrpx" else ".yrp"
    with NamedTemporaryFile(dir=destination.parent, prefix=".replay-", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        with ZipFile(staged, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("replay" + suffix, data)
            archive.writestr(
                "notes.json", json.dumps(asdict(entry.annotation), ensure_ascii=False, indent=2)
            )
        _publish(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
    return destination

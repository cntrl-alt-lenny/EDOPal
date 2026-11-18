"""Serialized, cancellable changes to marked files in the game's root list folder."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event
from zipfile import ZipFile

from music import settings
from state_lock import appearance_lock

from .catalogue import Catalogue, filename, sha, valid_mark, validate
from .files import staged_sibling


class State(StrEnum):
    ABSENT = "Available"
    INSTALLED = "Installed"
    OLD = "Update available"
    MODIFIED = "Edited or occupied"


class GameBlocked(RuntimeError):
    pass


def _folder(game: Path) -> Path:
    if not game.is_dir():
        raise ValueError("Select an available game folder first.")
    path = game.resolve() / "lflists"
    if path.resolve() != path or path.is_symlink():
        raise ValueError("The game’s list folder points to another location.")
    return path


def _inspect(folder: Path, row: dict) -> tuple[State, str]:
    path = folder / filename(row["id"])
    try:
        info = path.lstat()
    except FileNotFoundError:
        return State.ABSENT, ""
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4 << 20 or path.resolve() != path:
        return State.MODIFIED, ""
    data = path.read_bytes()
    if not valid_mark(data, row["id"]):
        return State.MODIFIED, ""
    checksum = sha(data)
    return (State.INSTALLED if checksum == row["sha256"] else State.OLD), checksum


def statuses(game: Path, rows: Sequence[dict]) -> dict[int, State]:
    folder = _folder(game)
    return {r["id"]: _inspect(folder, r)[0] for r in rows if "file" in r}


@dataclass
class PreviousFile:
    game: Path
    data: bytes | None
    digest: str
    expected_after: str


@dataclass
class Changes:
    changed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    stopped: bool = False
    previous: dict[int, PreviousFile] = field(default_factory=dict)


def _publish(
    folder: Path, row: dict, data: bytes, previous: tuple[State, str], earlier_digest: str = ""
) -> None:
    target = folder / filename(row["id"])
    with staged_sibling(target) as stage:
        stage.write_bytes(data)
        validate(stage, row, earlier_digest=earlier_digest)
        if _inspect(folder, row) != previous:
            raise ValueError(f"{row['name']} changed while it was being added. Your copy was kept.")
        if previous[0] == State.ABSENT:
            # A new file appearing after inspection must win over our publication.
            if os.name == "nt":
                stage.rename(target)
            else:
                os.link(stage, target)
        else:
            stage.replace(target)


def _closed(game: Path) -> None:
    status = settings.game_process_status(game)
    if status == settings.GameProcessStatus.RUNNING:
        raise GameBlocked("EDOPro is open in this installation. Close the game, then retry.")
    if status != settings.GameProcessStatus.CLOSED:
        raise GameBlocked("Couldn’t check whether EDOPro is open. Close the game, then retry.")


def change(
    game: Path,
    catalogue: Catalogue,
    rows: Sequence[dict],
    *,
    remove: bool = False,
    cancel: Event | None = None,
    progress: Callable[[tuple[int, int, str]], None] | None = None,
    undo: dict[int, PreviousFile] | None = None,
    receipt: Changes | None = None,
) -> Changes:
    folder = _folder(game)
    rows = [r for r in rows if "file" in r]
    if undo is not None and any(undo[r["id"]].game != folder.parent for r in rows):
        raise ValueError("The saved lists belong to a different game folder.")
    result = receipt if receipt is not None else Changes()
    _closed(game)
    with (
        appearance_lock(game),
        nullcontext(None)
        if remove or undo is not None
        else ZipFile(catalogue.directory / "lists.zip") as archive,
    ):
        for i, row in enumerate(rows, 1):
            if cancel and cancel.is_set():
                result.stopped = True
                break
            if i > 1:
                _closed(game)
            _folder(game)
            previous = _inspect(folder, row)
            state = previous[0]
            fid = row["id"]
            saved = undo[fid] if undo is not None else None
            deleting = saved.data is None if saved else remove
            target = folder / filename(fid)
            if state == State.MODIFIED or (saved and previous[1] != saved.expected_after):
                result.kept.append(row["name"])
            elif (deleting and state != State.ABSENT) or (
                not deleting and (saved or state != State.INSTALLED)
            ):
                before = None if state == State.ABSENT else target.read_bytes()
                if before is not None and sha(before) != previous[1]:
                    raise ValueError(f"{row['name']} changed while preparing the operation.")
                if deleting:
                    if _inspect(folder, row) != previous:
                        raise ValueError(
                            f"{row['name']} changed before removal. Your copy was kept."
                        )
                    target.unlink()
                    after = ""
                else:
                    data = saved.data if saved else archive.read(row["file"])
                    after = saved.digest if saved else row["sha256"]
                    folder.mkdir(exist_ok=True)
                    _publish(folder, row, data, previous, saved.digest if saved else "")
                result.previous[fid] = PreviousFile(folder.parent, before, previous[1], after)
                result.changed.append(row["name"])
            if progress:
                progress((i, len(rows), row["name"]))
    return result

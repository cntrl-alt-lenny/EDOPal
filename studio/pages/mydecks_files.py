"""Installation-scoped file actions for deck copies created in this session."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from decks import Preview, save_copy
from decks.sources import identity
from music.settings import _game_running
from tournament_decks import MAX_YDK


@dataclass(frozen=True)
class Receipt:
    path: Path
    stamp: tuple[int, ...]
    digest: bytes


def require_closed(game: Path) -> None:
    if _game_running(game):
        raise ValueError("Close this copy of EDOPro, then choose Retry.")


def publish(game: Path, name: str, reviewed: Preview) -> Receipt:
    require_closed(game)
    published = save_copy(game, name, reviewed)
    path = game / "deck" / published.name
    # Publishing has already succeeded. A concurrent move must not invite a second save.
    try:
        return Receipt(path, identity(published), _digest(published))
    except OSError:
        return Receipt(path, (), b"")


def _digest(path: Path) -> bytes:
    with path.open("rb") as stream:
        return hashlib.sha256(stream.read(MAX_YDK + 1)).digest()


def remove_copy(game: Path, receipt: Receipt) -> None:
    require_closed(game)
    path = receipt.path
    if (
        path.is_symlink()
        or path.parent != game / "deck"
        or not path.resolve().is_relative_to(game)
        or identity(path) != receipt.stamp
        or _digest(path) != receipt.digest
    ):
        raise ValueError("This copy has changed since it was saved. Your edits have been kept.")
    path.unlink()


def reveal_file(path: Path) -> None:
    if not path.is_file():
        raise OSError("This deck is no longer here. Refresh the library to see your current decks.")
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        try:
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.FileManager1",
                    "--object-path",
                    "/org/freedesktop/FileManager1",
                    "--method",
                    "org.freedesktop.FileManager1.ShowItems",
                    str([path.as_uri()]),
                    "",
                ],
                capture_output=True,
                check=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            subprocess.Popen(["xdg-open", str(path.parent)])

"""Shared limits, path checks and bounded file I/O for portable snapshots."""

import errno
import hashlib
import os
import re
import stat
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from config import user_data_dir

MAX_FILES = 8192
MAX_FILE = 128 << 20
MAX_TOTAL = 1800 << 20
MAX_MANIFEST = 8 << 20
CHUNK = 256 << 10
DEFAULTS = ("decks", "replays")
CATEGORIES = (*DEFAULTS, "puzzles", "screenshots", "studio", "preferences", "audio")
Digest = re.compile(r"[0-9a-f]{64}\Z")
Cancel = Callable[[], bool] | None
Progress = Callable[[int, int, str], None] | None


class Cancelled(Exception):
    """A cooperative stop. Recover a pending transaction after publication begins."""


class GameRunning(OSError):
    """The selected EDOPro installation must be closed before publication."""


def check(cancel: Cancel) -> None:
    if cancel and cancel():
        raise Cancelled("Backup operation cancelled")


def folded(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def portable(name: str) -> None:
    if not isinstance(name, str) or len(name.encode("utf-8")) > 768:
        raise ValueError("Invalid path length")
    parts = name.split("/")
    if len(parts) > 24:
        raise ValueError("Too many directory levels")
    for part in parts:
        if (
            not part
            or part.startswith(".")
            or part.endswith((" ", "."))
            or len(part.encode("utf-8")) > 240
            or PureWindowsPath(part).is_reserved()
            or part.split(".")[0].rstrip().upper() in {"CONIN$", "CONOUT$"}
            or re.search(r'[\\:<>"|?*\x00-\x1f\x7f]', part)
            or re.search(r"~\d", part)  # Windows short-name aliases can bypass a preview.
        ):
            raise ValueError(f"Non-portable path: {name!r}")


def linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def safe(root: Path, relative: str) -> Path:
    """Caller supplies validated relative components, including internal state names."""
    path = root
    for part in relative.split("/"):
        if (
            not part
            or part in {".", ".."}
            or Path(part).name != part
            or ":" in part
            or "\\" in part
        ):
            raise ValueError(f"Invalid relative path: {relative}")
        path = path / part
        if linked(path):
            raise ValueError(f"Linked path: {path}")
    if path.resolve() != path:
        raise ValueError(f"Redirected path: {path}")
    return path


@dataclass(frozen=True)
class Installation:
    game: Path
    data: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "game", Path(self.game).resolve(strict=True))
        object.__setattr__(self, "data", Path(self.data).resolve())
        if not self.game.is_dir():
            raise ValueError("Select an installation directory")

    @classmethod
    def at(cls, game: Path, config: Path | None = None) -> "Installation":
        return cls(game, config.parent if config else Path(user_data_dir()))

    def target(self, name: str) -> Path:
        scope, rel = name.split("/", 1)
        if scope not in {"game", "studio"}:
            raise ValueError(f"Unknown scope: {scope}")
        return safe(self.game if scope == "game" else self.data, rel)


def read_small(path: Path, limit: int) -> bytes:
    if not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File too large: {path}")
    return data


def transfer(
    source: BinaryIO, output: BinaryIO | None, limit: int, cancel: Cancel
) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    while True:
        check(cancel)
        block = source.read(min(CHUNK, limit - size + 1))
        if not block:
            return size, digest.hexdigest()
        size += len(block)
        if size > limit:
            raise ValueError("File exceeds its declared size or safety limit")
        digest.update(block)
        if output:
            output.write(block)


def signature(path: Path, cancel: Cancel = None) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError(f"Expected a bounded regular file: {path}")
    with path.open("rb") as stream:
        return transfer(stream, None, MAX_FILE, cancel)[1]


def publish_new(temp: Path, target: Path) -> None:
    if os.name == "nt":
        temp.rename(target)
        return
    try:
        os.link(temp, target)
    except OSError as exc:
        if exc.errno not in {errno.EPERM, errno.ENOTSUP, errno.EACCES}:
            raise
        # FAT and exFAT lack hard links. Use the OS exclusive rename so a failed
        # or interrupted USB transfer cannot expose an incomplete final archive.
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        source, dest = os.fsencode(temp), os.fsencode(target)
        if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
            result = libc.renameat2(-100, source, -100, dest, 1)  # AT_FDCWD, RENAME_NOREPLACE
        elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
            result = libc.renamex_np(source, dest, 4)  # RENAME_EXCL
        else:
            raise
        if result:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(target)) from exc

"""Ordering for file-backed playlist transactions.

Flush newly staged bytes before publishing their names. On POSIX, sync both
directories of a rename and persist new directory entries before journalling.
Windows has no portable directory fsync, and some POSIX volumes reject it.
Originals are renamed in place. Their pre-existing bytes depend on their writer.
The moves are individually atomic. Recovery checks the journal after interruption.
Persistence across power loss depends on the filesystem and device honoring flushes.
"""

import errno
import os
import shutil
import sys
from pathlib import Path


def sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(fd)


def ensure_directory(directory: Path) -> None:
    if not directory.is_dir():
        ensure_directory(directory.parent)
        directory.mkdir(exist_ok=True)
        sync_directory(directory.parent)


def copy_synced(source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("wb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())


def _volume(directory: Path) -> tuple[int, bytes]:
    mount = b""
    if sys.platform.startswith("linux"):
        # Two bind mounts can have the same st_dev and still reject a rename.
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            info = Path(f"/proc/self/fdinfo/{fd}").read_bytes().splitlines()
            mount = next((line for line in info if line.startswith(b"mnt_id:")), b"")
            if not mount:
                raise OSError("Could not verify the playlist recovery filesystem.")
        finally:
            os.close(fd)
    return directory.stat().st_dev, mount


def check_volume(state: Path, folders: list[Path]) -> None:
    device = _volume(state)
    for folder in folders:
        while not folder.exists():
            folder = folder.parent
        if _volume(folder) != device:
            raise OSError("The playlist and its recovery folder must be on the same filesystem.")

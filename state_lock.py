"""OS-owned locks for local game and library updates."""

import contextlib
import sys
from pathlib import Path


@contextlib.contextmanager
def locked(directory: Path, filename: str = ".music.lock"):
    """An OS-owned lock is automatically released if this process exits."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / filename).open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

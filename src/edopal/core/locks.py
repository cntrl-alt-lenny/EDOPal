"""OS-owned locks shared by storage, launch dispatch and pack cache writers."""

from __future__ import annotations

import errno
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

INSTALL_LOCK_NAME = ".edopal.lock"


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Wait for an exclusive lock. The stable lock file must never be replaced."""
    if path.is_symlink() or path.resolve() != path.parent.resolve() / path.name:
        raise ValueError(f"Redirected lock file: {path}")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Expected a regular lock file: {path}")
        if os.name == "nt":
            import msvcrt

            # Windows permits byte-range locks past EOF, including on an empty file.
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # releases the lock, including when the process dies


def install_lock(root: Path):
    """Serialize launch and publication across profiles and aliases of one installation."""
    return file_lock(root.resolve(strict=True) / INSTALL_LOCK_NAME)

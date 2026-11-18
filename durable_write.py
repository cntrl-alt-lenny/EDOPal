"""Publish complete sibling files and order local filesystem updates."""

import errno
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from music.storage import sync_directory
from state_lock import locked


def durable_write(
    path: Path, data: bytes, *, publish: Callable[[Path, Path], None] = os.replace
) -> None:
    """Flush the staged bytes before making the final name visible."""
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    stage = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        publish(stage, path)
    finally:
        stage.unlink(missing_ok=True)
    # POSIX persists the name where directory fsync is supported. Windows has
    # no directory fsync: we rely on the flushed file and atomic same-volume
    # promotion, with name persistence after power loss left to the filesystem.
    sync_directory(path.parent)


@contextmanager
def waiting_lock(path: Path) -> Iterator[None]:
    """Queue short publications using the same OS locks as the other writers."""
    # locked() creates the directory tree. Persist newly created names before
    # publishing into them, including a first save to a new profile.
    missing = []
    for parent in path.absolute().parents:
        if parent.is_dir():
            break
        missing.append(parent)
    deadline = time.monotonic() + 10
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(locked(path.parent, path.name))
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)
        for directory in reversed(missing):
            sync_directory(directory.parent)
        yield

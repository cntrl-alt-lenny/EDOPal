"""OS-owned locks for local game and library updates."""

import contextlib
import functools
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Concatenate, ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


@contextlib.contextmanager
def appearance_lock(game: Path) -> Iterator[None]:
    root = game.resolve()
    lock = root / ".edopro-appearance.lock"
    if lock.resolve() != lock or lock.is_symlink():
        raise OSError("Invalid installation lock file.")
    with locked(root, lock.name):
        yield


def appearance_writer(
    func: Callable[Concatenate[str, _P], _T],
) -> Callable[Concatenate[str, _P], _T]:
    @functools.wraps(func)
    def wrapper(edopro_path: str, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        with appearance_lock(Path(edopro_path)):
            return func(edopro_path, *args, **kwargs)

    return wrapper


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

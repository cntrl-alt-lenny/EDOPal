"""Persistent, disposable fingerprints for browsing recordings."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from music.audio import _digest
from music.catalogue import atomic_json, library_root
from state_lock import locked

_LIMIT = 8192
_MAX_BYTES = 4 << 20
_HEX = re.compile("[0-9a-f]{64}")


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _change_time(path: Path, info: os.stat_result) -> int:
        # Python 3.11/3.12 report creation time as st_ctime on Windows. NTFS
        # ChangeTime also changes on an in-place rewrite with its mtime restored.
        handle = _kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            return 0
        try:
            basic = (
                ctypes.c_int64 * 5
            )()  # FILE_BASIC_INFO, ChangeTime is its fourth LARGE_INTEGER.
            if _kernel.GetFileInformationByHandleEx(handle, 0, basic, ctypes.sizeof(basic)):
                return basic[3]
            return 0
        finally:
            _kernel.CloseHandle(handle)
else:

    def _change_time(path: Path, info: os.stat_result) -> int:
        return info.st_ctime_ns


def _stamp(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, _change_time(path, info)]


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return {}
        rows = json.loads(raw)
        if isinstance(rows, dict) and len(rows) <= _LIMIT:
            return rows
    except (OSError, ValueError, RecursionError):
        pass
    return {}


def _merge(path: Path, changes: dict) -> None:
    # Only the short merge/promotion holds the cross-process lock. A busy or
    # read-only profile can still browse using the digests it just computed.
    deadline = time.monotonic() + 1
    while True:
        try:
            with locked(path.parent, ".music-digests.lock"):
                rows = _read(path)
                for key, row in changes.items():
                    rows.pop(key, None)
                    rows[key] = row
                atomic_json(path, dict(list(rows.items())[-_LIMIT:]))
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN) or time.monotonic() >= deadline:
                return
            time.sleep(0.02)


@contextmanager
def browsing_digests(
    root: Path, checkpoint: Callable[[], None] | None = None
) -> Iterator[Callable[[Path], str]]:
    profile = root.parent if root.name == "soundtracks" else library_root().parent
    path = profile / "music-digests-v1.json"
    known = _read(path)
    changes: dict = {}

    def lookup(recording: Path) -> str:
        if checkpoint:
            checkpoint()
        recording = recording.absolute()
        key = hashlib.sha256(os.fsencode(recording)).hexdigest()
        for _ in range(2):
            stamp = _stamp(recording)
            if not stamp[1] or not stamp[-1]:
                return _digest(recording)
            row = changes.get(key, known.get(key))
            if isinstance(row, list) and len(row) == 3 and row[0] == stamp:
                digest = row[1]
                if (
                    isinstance(digest, str)
                    and _HEX.fullmatch(digest)
                    and row[2] == _seal(key, stamp, digest)
                ):
                    return digest
            digest = _digest(recording)
            if _stamp(recording) == stamp:
                changes[key] = [stamp, digest, _seal(key, stamp, digest)]
                if len(changes) > _LIMIT:
                    del changes[next(iter(changes))]
                return digest
        raise OSError(f"{recording.name} changed while reading the soundtrack library. Try again.")

    yield lookup
    if changes:
        _merge(path, changes)


def _seal(key: str, stamp: list[int], digest: str) -> str:
    return hashlib.sha256(f"{key}:{stamp}:{digest}".encode("ascii")).hexdigest()

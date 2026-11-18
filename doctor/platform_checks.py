"""Read executable versions and assess access without writing a probe file."""

import ctypes
import os
import plistlib
import sys
from ctypes import wintypes
from pathlib import Path

from .game_data import local_path


def game_executable(root: Path) -> Path | None:
    candidates = (
        root / "EDOPro.exe",
        root / "EDOPro",
        root / "edopro",
        root / "ygopro",
        root / "EDOPro.app" / "Contents" / "Info.plist",
        root / "Contents" / "Info.plist",
    )
    for path in candidates:
        try:
            candidate = local_path(root, path)
            if candidate.is_file():
                if (
                    path.parent.parent == root
                    and path.name == "Info.plist"
                    and root.suffix.lower() != ".app"
                ):
                    continue
                return candidate
        except (OSError, ValueError):
            continue
    return None


def _windows_version(path: Path) -> str | None:
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = (
        wintypes.LPCVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    block = ctypes.create_string_buffer(size)
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    if (
        not version.GetFileVersionInfoW(str(path), 0, size, block)
        or not version.VerQueryValueW(block, "\\", ctypes.byref(value), ctypes.byref(length))
        or length.value < 52
        or not value
    ):
        return None
    fixed = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD))
    if fixed[0] != 0xFEEF04BD:
        return None
    ms, ls = fixed[2], fixed[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def game_version(executable: Path) -> str | None:
    try:
        if executable.name == "Info.plist":
            with executable.open("rb") as stream:
                values = plistlib.load(stream)
            if isinstance(values, dict):
                value = values.get("CFBundleShortVersionString") or values.get("CFBundleVersion")
                return value if isinstance(value, str) else None
        if sys.platform == "win32" and executable.suffix.lower() == ".exe":
            return _windows_version(executable)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return None


def directory_writable(path: Path) -> bool | None:
    if sys.platform != "win32":
        return os.access(path, os.W_OK | os.X_OK)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    # Request the directory's add-file and add-directory rights on an existing handle.
    handle = kernel.CreateFileW(str(path), 0x06, 0x07, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        return False if ctypes.get_last_error() in (5, 19) else None
    kernel.CloseHandle(handle)
    return True


def lock_active(path: Path) -> bool | None:
    try:
        with path.open("rb", buffering=0) as stream:
            if sys.platform == "win32":
                return _windows_byte_locked(stream.fileno())
            else:
                import fcntl

                # The existing writer uses an exclusive flock, even after the marker is created.
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError as exc:
        return True if getattr(exc, "winerror", None) == 33 else None
    return False


def _windows_byte_locked(descriptor: int) -> bool | None:
    import msvcrt

    # The CRT maps lock violations to generic access errors. ReadFile retains the cause.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel.ReadFile.restype = wintypes.BOOL
    byte = ctypes.create_string_buffer(1)
    count = wintypes.DWORD()
    if kernel.ReadFile(msvcrt.get_osfhandle(descriptor), byte, 1, ctypes.byref(count), None):
        return False
    return True if ctypes.get_last_error() == 33 else None

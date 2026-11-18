"""Identify the selected EDOPro client from process information visible to Studio."""

from __future__ import annotations

import os
import subprocess
import sys
from enum import IntEnum
from pathlib import Path, PurePosixPath, PureWindowsPath


class GameProcessStatus(IntEnum):
    CLOSED = 0
    RUNNING = 1
    UNKNOWN = 2


class GameProcessUnknown(OSError):
    """A write gate could not rule out an active client."""


_NAMES = {"edopro", "ygopro", "edopro.exe", "ygopro.exe"}
_PROC = Path("/proc")


def _in_game(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root)


def _cwd_matches(proc: Path, root: Path) -> bool:
    try:
        return _in_game((proc / "cwd").readlink(), root)
    except OSError:
        return False


def _wine_path(proc: Path, path: PureWindowsPath) -> Path | None:
    if not path.is_absolute() or len(path.drive) != 2:
        return None
    # Wine's per-prefix drive mappings also apply to Proton. Never assume
    # that an arbitrary C: path belongs to the installation being edited.
    try:
        with (proc / "environ").open("rb") as stream:
            env = dict(
                item.split(b"=", 1) for item in stream.read(128 * 1024).split(b"\0") if b"=" in item
            )
        prefix = env.get(b"WINEPREFIX")
        if not prefix and env.get(b"STEAM_COMPAT_DATA_PATH"):
            prefix = env[b"STEAM_COMPAT_DATA_PATH"] + b"/pfx"
        if prefix:
            drive = Path(os.fsdecode(prefix)) / "dosdevices" / path.drive.lower()
            try:
                return drive.resolve(strict=True).joinpath(*path.parts[1:])
            except OSError:
                return None
    except PermissionError:
        return None
    except FileNotFoundError:
        pass
    if path.drive.lower() == "z:":
        return Path("/", *path.parts[1:])
    return None


def _wine_status(proc: Path, root: Path, named: bool) -> GameProcessStatus:
    try:
        with (proc / "cmdline").open("rb") as stream:
            command = stream.read(128 * 1024)
    except OSError:
        return GameProcessStatus.UNKNOWN
    # Wine can change the task name and argv[0]. Look for the client itself,
    # including a Unix path passed to a loader and a path in Wine's Z: drive.
    unknown = False
    other = False
    for raw in command.split(b"\0"):
        arg = os.fsdecode(raw)
        windows = PureWindowsPath(arg)
        if windows.name.casefold() not in _NAMES:
            continue
        if arg.startswith("/"):
            native = Path(arg)
        else:
            native = _wine_path(proc, windows)
            if native is None:
                unknown = True
                continue
        if _in_game(native, root):
            return GameProcessStatus.RUNNING
        # A Wine drive can resolve client path components case-insensitively.
        if PureWindowsPath(native).is_relative_to(PureWindowsPath(root)):
            unknown = True
        other = True
    if (unknown or named or other) and _cwd_matches(proc, root):
        return GameProcessStatus.RUNNING
    if unknown or (named and not other) or not command.strip(b"\0") or len(command) == 128 * 1024:
        return GameProcessStatus.UNKNOWN
    return GameProcessStatus.CLOSED


def _linux_process(proc: Path, root: Path) -> GameProcessStatus:
    try:
        comm = (proc / "comm").read_bytes().rstrip(b"\n").lower()
    except OSError:
        comm = b""
    named = os.fsdecode(comm) in _NAMES
    try:
        exe = Path(str((proc / "exe").readlink()).removesuffix(" (deleted)"))
    except OSError:
        if named or comm.startswith(b"wine"):
            return _wine_status(proc, root, named)
        if not comm and proc.exists():
            return GameProcessStatus.UNKNOWN
        return GameProcessStatus.CLOSED
    if exe.name.casefold() in _NAMES:
        if _in_game(exe, root) or _cwd_matches(proc, root):
            return GameProcessStatus.RUNNING
        # Packaged executables can be shared by separate writable game data roots.
        if exe.as_posix().startswith(("/app/", "/usr/", "/nix/store/")) or "flatpak" in exe.parts:
            return GameProcessStatus.UNKNOWN
        return GameProcessStatus.CLOSED
    if named or exe.name.casefold().startswith("wine") or comm.startswith(b"wine"):
        return _wine_status(proc, root, named)
    if not comm:
        return GameProcessStatus.UNKNOWN
    return GameProcessStatus.CLOSED


def _linux_game_running(root: Path, processes: Path) -> GameProcessStatus:
    result = GameProcessStatus.CLOSED
    visible = False
    for pid in processes.iterdir():
        if pid.name.isdecimal():
            visible = True
            status = _linux_process(pid, root)
            if status == GameProcessStatus.RUNNING:
                return status
            if status == GameProcessStatus.UNKNOWN:
                result = status
    return result if visible else GameProcessStatus.UNKNOWN


def _windows_image(pid: int) -> str:
    # Get-Process.Path uses MainModule and can fail for a suspended or different-bitness
    # client. The image query only needs PROCESS_QUERY_LIMITED_INFORMATION.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value
    finally:
        kernel.CloseHandle(handle)


def _listed_processes(root: Path, listing: str, platform: str) -> GameProcessStatus:
    if platform == "darwin" and not listing.strip():
        # ps lists itself, while the filtered Windows query can legitimately return no rows.
        return GameProcessStatus.UNKNOWN
    result = GameProcessStatus.CLOSED
    for line in listing.splitlines():
        if platform == "win32":
            try:
                line = _windows_image(int(line))
            except (OSError, ValueError):
                result = GameProcessStatus.UNKNOWN
                continue
            absolute = PureWindowsPath(line).is_absolute()
        else:
            absolute = PurePosixPath(line.strip()).is_absolute()
        path = Path(line.strip())
        if path.name.casefold() not in _NAMES:
            if platform == "win32":  # PowerShell already filtered on process name.
                result = GameProcessStatus.UNKNOWN
            continue
        if not absolute or "\ufffd" in line:
            result = GameProcessStatus.UNKNOWN
        elif _in_game(path, root):
            return GameProcessStatus.RUNNING
        elif platform == "darwin" and any(p.lower().endswith(".app") for p in path.parts):
            result = GameProcessStatus.UNKNOWN
    return result


def game_process_status(game: Path) -> GameProcessStatus:
    """Inspect the process table once. Call in a worker immediately before a write."""
    try:
        root = game.resolve()
        if sys.platform.startswith("linux"):
            status = _linux_game_running(root, _PROC)
            if status == GameProcessStatus.CLOSED:
                # A restricted PID view can omit the client altogether.
                mounts = (_PROC / "self/mountinfo").read_bytes().splitlines()
                if (_PROC / "self/root/.flatpak-info").exists() or any(
                    b" - proc " in line
                    and b"hidepid=" in line
                    and b"hidepid=0" not in line
                    and b"hidepid=off" not in line
                    for line in mounts
                ):
                    return GameProcessStatus.UNKNOWN
            return status
        if sys.platform == "win32":
            # Enumerating first distinguishes an empty match from a failed listing.
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
                "Get-Process | Where-Object {$_.ProcessName -in 'EDOPro','ygopro'} | "
                "ForEach-Object {$_.Id}",
            ]
        elif sys.platform == "darwin":
            command = ["ps", "-axww", "-o", "comm="]
        else:
            return GameProcessStatus.UNKNOWN
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            timeout=8,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return _listed_processes(root, result.stdout, sys.platform)
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError):
        return GameProcessStatus.UNKNOWN


def is_game_running(game: Path) -> bool:
    """Return a confirmed result or raise GameProcessUnknown so writes fail closed."""
    status = game_process_status(game)
    if status == GameProcessStatus.UNKNOWN:
        raise GameProcessUnknown(
            "Could not determine whether EDOPro is open. Close the game and retry."
        )
    return status == GameProcessStatus.RUNNING

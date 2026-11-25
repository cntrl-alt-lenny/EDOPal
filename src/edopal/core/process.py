"""Identify a running copy of EDOPro without starting or attaching to it."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
import time
from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE
from dataclasses import dataclass
from io import StringIO
from pathlib import Path, PurePath, PureWindowsPath

from edopal.core.install import Install, RunState

_NAMES = {"edopro", "ygopro", "edopro.exe", "ygopro.exe"}
_PROC = Path("/proc")
_HOST_PROC = Path("/run/host/proc")
_CONTAINER = Path("/run/.containerenv")
_LIMIT = 128 * 1024
_TIMEOUT = 2.0


def _canonical(value: str | Path) -> PurePath:
    if sys.platform != "win32":
        return Path(value).resolve()
    # Compare the same DOS/UNC spelling for a settings path and a full image
    # query. On Windows resolve junctions and short (8.3) names first.
    path = PureWindowsPath(os.path.realpath(value) if os.name == "nt" else value).as_posix()
    if path.casefold().startswith("//?/unc/"):
        path = "//" + path[8:]
    return PureWindowsPath(path.removeprefix("//?/"))


@dataclass(frozen=True)
class _Client:
    root: PurePath
    exe: PurePath | None
    bundle: PurePath | None
    names: set[str]

    @classmethod
    def from_install(cls, game: Install) -> _Client:
        argv = game.launch.argv if game.launch else ()
        executable = game.extra.get("executable")
        bundle = game.extra.get("bundle")
        if not executable and argv and (game.kind in ("native", "windows") or argv[1:2] == ("-C",)):
            executable = argv[0]
        if not bundle and game.kind == "macos-app" and "-a" in argv:
            args = argv[argv.index("-a") + 1 :]
            bundle = args[0] if args else None
        exe = _canonical(executable) if executable else None
        names = _NAMES | {exe.name.casefold()} if exe else _NAMES
        return cls(_canonical(game.root), exe, _canonical(bundle) if bundle else None, names)

    def matches(self, image: Path) -> bool:
        image = _canonical(image)
        return image == self.exe or image.parent == self.root


def _link(path: Path) -> Path | None:
    try:
        return Path(str(path.readlink()).removesuffix(" (deleted)"))
    except OSError:
        return None


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError("Process metadata truncated")
    return data


def _wine_path(pid: Path, value: str) -> Path | None:
    if value.startswith("/"):
        return Path(value)
    win = PureWindowsPath(value)
    if not win.is_absolute() or len(win.drive) != 2:
        return None
    try:
        env = dict(item.split(b"=", 1) for item in _read(pid / "environ").split(b"\0") if b"=" in item)
    except FileNotFoundError:
        env = {}
    prefix = env.get(b"WINEPREFIX")
    if not prefix and env.get(b"STEAM_COMPAT_DATA_PATH"):
        prefix = env[b"STEAM_COMPAT_DATA_PATH"] + b"/pfx"
    if not prefix and env.get(b"HOME"):
        prefix = env[b"HOME"] + b"/.wine"
    if prefix:
        drive = Path(os.fsdecode(prefix)) / "dosdevices" / win.drive.lower()
        if drive.is_dir():
            return drive.resolve().joinpath(*win.parts[1:])
        # A custom prefix may map Z: somewhere other than /.
        return None
    if win.drive.casefold() == "z:":
        return Path("/", *win.parts[1:])
    return None


def _linux_client(pid: Path, client: _Client, image: Path | None, named: bool, wine: bool) -> RunState:
    # A shared binary can serve several -C roots. A known game process with the
    # selected cwd is positive evidence even when its image link is unreadable.
    known = named or (image is not None and image.name.casefold() in client.names)
    cwd = _link(pid / "cwd")
    if known and cwd and cwd.resolve() == client.root:
        return RunState.RUNNING
    try:
        args = [os.fsdecode(part).strip('"') for part in _read(pid / "cmdline").split(b"\0") if part]
    except (OSError, ValueError):
        args = []
    # Wine may change argv[0] and comm. Only inspect argv of a client or Wine
    # loader, so an editor or shell mentioning the game cannot block writes.
    # The first executable in a loader's argv identifies the guest. Later
    # arguments might just be filenames being edited in another Wine program.
    guest = next((arg for arg in args if PureWindowsPath(arg).suffix.casefold() == ".exe"), None)
    if guest is None and args:
        guest = args[0]
    candidate = guest if guest and PureWindowsPath(guest).name.casefold() in client.names else None
    if not known and not candidate:
        return RunState.CLOSED if args else RunState.UNKNOWN
    if cwd and cwd.resolve() == client.root:
        return RunState.RUNNING
    if "-C" in args:
        index = args.index("-C") + 1
        if index < len(args):
            data = _wine_path(pid, args[index]) if wine else Path(args[index])
            if data and data.is_absolute():
                resolved = data.resolve()
                if resolved == client.root:
                    return RunState.RUNNING
                if wine and PureWindowsPath(resolved) == PureWindowsPath(client.root):
                    return RunState.UNKNOWN
                return RunState.CLOSED
        return RunState.UNKNOWN

    if image is not None and image.name.casefold() in client.names:
        if client.matches(image):
            return RunState.RUNNING
        if cwd is None and not args:
            return RunState.UNKNOWN  # A hidden -C argument can point at the selected root.
        if image.parts[1:2] in (("app",), ("usr",), ("nix",)) or "flatpak" in image.parts:
            return RunState.UNKNOWN if cwd is None else RunState.CLOSED
        return RunState.CLOSED

    if not candidate:
        return RunState.UNKNOWN
    path = _wine_path(pid, candidate) if wine else Path(candidate)
    if not path or not path.is_absolute():
        return RunState.UNKNOWN
    if client.matches(path):
        return RunState.RUNNING
    win = PureWindowsPath(path.resolve())
    if wine and (win.is_relative_to(PureWindowsPath(client.root)) or (client.exe and win == PureWindowsPath(client.exe))):
        # The Wine drive can be case-insensitive on a case-sensitive host.
        return RunState.UNKNOWN
    return RunState.CLOSED


def _linux_process(pid: Path, client: _Client) -> RunState:
    try:
        comm = os.fsdecode((pid / "comm").read_bytes()).rstrip("\n").casefold()
    except OSError:
        comm = ""
    image = _link(pid / "exe")
    name = image.name.casefold() if image else ""
    named = comm in client.names
    if name not in client.names and not named and (comm.endswith(".exe") or comm in ("wineserver", "wineserver64")):
        # Wine gives identifiable guest processes their Windows image name in
        # comm. An inaccessible unrelated guest or wineserver can be skipped.
        return RunState.CLOSED
    wine = name.startswith("wine") or comm.startswith("wine") or (named and comm.endswith(".exe"))
    if named or name in client.names or wine:
        if image is None:
            # A zombie remains in task listings until its parent reaps it.
            try:
                fields = (pid / "stat").read_bytes().rsplit(b") ", 1)[-1].split()
                if fields and fields[0] in (b"Z", b"X"):
                    return RunState.CLOSED
            except OSError:
                pass
        try:
            return _linux_client(pid, client, image, named, wine)
        except (OSError, ValueError):
            return RunState.UNKNOWN
    if not comm and not image and pid.exists():
        return RunState.UNKNOWN
    return RunState.CLOSED


def _linux_running(client: _Client) -> RunState:
    in_container = _CONTAINER.exists()
    host_visible = in_container and _HOST_PROC.is_dir()
    table = _HOST_PROC if host_visible else _PROC
    deadline = time.monotonic() + _TIMEOUT
    result = RunState.CLOSED
    seen = False
    for pid in table.iterdir():
        if pid.name.isdecimal():
            if time.monotonic() > deadline:
                return RunState.UNKNOWN
            seen = True
            state = _linux_process(pid, client)
            if state == RunState.RUNNING:
                return state
            if state == RunState.UNKNOWN:
                result = state

    if not seen or result == RunState.UNKNOWN or (in_container and not host_visible):
        return RunState.UNKNOWN
    if not host_visible and (_PROC / "self/root/.flatpak-info").exists():
        return RunState.UNKNOWN
    # Read the options for the actual proc mount. hidepid can silently omit
    # processes, so a negative match from that view cannot establish CLOSED.
    mounted = False
    for line in (_PROC / "self/mountinfo").read_bytes().splitlines():
        mount, _, fs = line.partition(b" - ")
        fields = mount.split()
        if len(fields) >= 6 and fields[4] == os.fsencode(table) and fs.startswith(b"proc "):
            mounted = True
            options = (fields[5] + b"," + fs.split()[-1]).split(b",")
            if any(opt.startswith(b"hidepid=") and opt not in (b"hidepid=0", b"hidepid=off") for opt in options):
                return RunState.UNKNOWN
    return RunState.CLOSED if mounted else RunState.UNKNOWN


def _windows_image(pid: int) -> str:
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
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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


def _windows_running(client: _Client) -> RunState:
    system = PureWindowsPath(os.environ.get("SystemRoot", r"C:\Windows"))
    if not system.is_absolute():
        return RunState.UNKNOWN
    output = subprocess.run(
        [str(system / "System32/tasklist.exe"), "/FO", "CSV", "/NH"],
        capture_output=True,
        check=True,
        timeout=_TIMEOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    raw = output.stdout
    encoding = "utf-16" if raw.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else ("oem" if os.name == "nt" else "latin-1")
    listing = raw.removeprefix(BOM_UTF8).decode(encoding)
    if output.stderr.strip() or not listing.endswith("\n") or "\0" in listing:
        return RunState.UNKNOWN
    seen: set[int] = set()
    candidates = []
    for row in csv.reader(StringIO(listing, newline=""), strict=True):
        if len(row) != 5 or any("\n" in field or "\r" in field for field in row):
            return RunState.UNKNOWN
        if not row[0].strip() or not row[1].isascii() or not row[1].isdecimal():
            return RunState.UNKNOWN
        pid = int(row[1])
        if pid in seen or pid > 0xFFFFFFFF:
            return RunState.UNKNOWN
        seen.add(pid)
        if row[0].casefold() in client.names:
            candidates.append(pid)
    if os.getpid() not in seen:
        return RunState.UNKNOWN
    result = RunState.CLOSED
    for pid in candidates:
        try:
            path = PureWindowsPath(_windows_image(pid))
        except OSError:
            result = RunState.UNKNOWN
            continue
        if not path.is_absolute() or path.name.casefold() not in client.names or "\ufffd" in str(path):
            result = RunState.UNKNOWN
        elif client.matches(Path(path)):
            return RunState.RUNNING
        elif client.exe is None:
            # The full image alone cannot rule out -C pointing to a database-only selection.
            result = RunState.UNKNOWN
    return result


def _mac_running(client: _Client) -> RunState:
    output = subprocess.run(
        ["/bin/ps", "-axww", "-o", "comm="], capture_output=True, check=True, encoding="utf-8", timeout=_TIMEOUT
    )
    if output.stderr.strip() or not output.stdout.strip():
        return RunState.UNKNOWN
    result = RunState.CLOSED
    for line in output.stdout.splitlines():
        path = Path(line.strip())
        if path.name.casefold() in client.names:
            if not path.is_absolute():
                result = RunState.UNKNOWN
            elif client.matches(path) or (client.bundle and path.resolve().parent == client.bundle / "Contents/MacOS"):
                return RunState.RUNNING
            elif not client.bundle and any(part.casefold().endswith(".app") for part in path.parts):
                result = RunState.UNKNOWN
    return result


def run_state(install: Install) -> RunState:
    """Probe the selected install once. Restricted or incomplete views yield UNKNOWN."""
    try:
        client = _Client.from_install(install)
        if sys.platform.startswith("linux"):
            return _linux_running(client)
        if sys.platform == "win32":
            return _windows_running(client)
        if sys.platform == "darwin":
            return _mac_running(client)
    except (OSError, ValueError, RuntimeError, csv.Error, subprocess.SubprocessError):
        pass
    return RunState.UNKNOWN

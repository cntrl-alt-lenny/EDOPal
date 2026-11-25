"""Read-only inspection of an EDOPro data folder, executable or macOS bundle."""

from __future__ import annotations

import ctypes
import json
import os
import plistlib
import re
import stat
import struct
import sys
import zipfile
from collections.abc import Iterator
from ctypes import wintypes
from functools import lru_cache
from itertools import islice
from pathlib import Path
from time import monotonic
from xml.parsers.expat import ExpatError

from edopal.core.install import Install, Kind, LaunchTarget

_NATIVE_NAMES = ("EDOPro", "edopro", "ygopro")
_WINDOWS_NAMES = ("EDOPro.exe", "edopro.exe", "ygopro.exe")
_CLIENT_NAMES = frozenset(name.casefold() for name in _NATIVE_NAMES + _WINDOWS_NAMES)
_BANNER = re.compile(rb"\x00EDOPro version (\d{1,5}\.\d{1,5}\.\d{1,5})(?=\x00)")
_MAX_BINARY = 128 * 1024**2
_CHUNK = 512 * 1024
_MAX_METADATA = 1024**2
_DATA_MARKERS = {"cards.cdb", "config", "expansions", "repositories"}


def is_game_executable(path: Path) -> bool:
    return path.name.casefold() in _CLIENT_NAMES


def _metadata(path: Path) -> bytes:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_METADATA:
        raise ValueError("Invalid metadata file")
    with path.open("rb") as stream:
        data = stream.read(_MAX_METADATA + 1)
    if len(data) > _MAX_METADATA:
        raise ValueError("Metadata is too large")
    return data


def _windows_version(path: Path) -> str | None:
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = (
        wintypes.LPCVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    version.VerQueryValueW.restype = wintypes.BOOL
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not 0 < size <= _MAX_METADATA:
        return None
    block = ctypes.create_string_buffer(size)
    address = ctypes.c_void_p()
    length = wintypes.UINT()
    if (
        version.GetFileVersionInfoW(str(path), 0, size, block)
        and version.VerQueryValueW(block, "\\", ctypes.byref(address), ctypes.byref(length))
        and length.value >= 52
        and address
    ):
        fixed = ctypes.cast(address, ctypes.POINTER(wintypes.DWORD))
        if fixed[0] == 0xFEEF04BD:
            ms, ls = fixed[2], fixed[3]
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    return None


def _plist_version(values: dict) -> str | None:
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:100]
    return None


@lru_cache(maxsize=64)
def _client_version(path: Path, size: int, stamp: tuple[int, int], platform: str) -> str | None:
    """Cache the scan while inode metadata is unchanged (discovery may reach many aliases)."""
    if path.name == "Info.plist":
        values = plistlib.loads(_metadata(path))
        return _plist_version(values) if isinstance(values, dict) else None
    if platform == "win32" and path.suffix.lower() == ".exe" and hasattr(ctypes, "WinDLL"):
        if version := _windows_version(path):
            return version
    if not 64 <= size <= _MAX_BINARY:
        return None
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64:
            return None
        if header.startswith(b"\x7fELF") and header[4] in (1, 2) and header[5] in (1, 2) and header[6] == 1:
            order = "little" if header[5] == 1 else "big"
            if int.from_bytes(header[16:18], order) not in (2, 3):  # executable / position-independent executable
                return None
        elif header.startswith(b"MZ") and path.suffix.lower() == ".exe":
            offset = int.from_bytes(header[60:64], "little")
            if not 64 <= offset <= size - 24:
                return None
            stream.seek(offset)
            if stream.read(4) != b"PE\x00\x00":
                return None
        else:
            return None
        stream.seek(0)
        remaining, tail, versions = size, b"", set()
        while remaining:
            chunk = stream.read(min(_CHUNK, remaining))
            if not chunk:
                return None
            remaining -= len(chunk)
            data = tail + chunk
            versions.update(_BANNER.findall(data))
            if len(versions) > 1:
                return None
            tail = data[-64:]  # Include banners that straddle two reads.
        return versions.pop().decode("ascii") if versions else None


def game_version(path: Path) -> str | None:
    """Read the compiled startup banner, Windows file version or macOS Info.plist."""
    try:
        info = path.stat()
        if stat.S_ISREG(info.st_mode):
            return _client_version(path, info.st_size, (info.st_mtime_ns, info.st_ctime_ns), sys.platform)
    except (OSError, ValueError, ExpatError, plistlib.InvalidFileException):
        pass
    return None


def _nonempty(path: Path) -> bool:
    try:
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and info.st_size > 0
    except OSError:
        return False


def _configured_repos(root: Path, deadline: float) -> Iterator[tuple[Path, Path | None]]:
    seen = set()
    for name in ("user_configs.json", "configs.json"):
        try:
            values = json.loads(_metadata(root / "config" / name))
        except (OSError, ValueError, RecursionError):
            continue
        entries = values.get("repos") if isinstance(values, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if monotonic() >= deadline:
                return
            if not isinstance(entry, dict):
                continue
            dest = entry.get("repo_path")
            if not dest:
                label = entry.get("repo_name") or Path(str(entry.get("url", "")).rstrip("/")).name
                if not isinstance(label, str) or not label:
                    continue
                dest = "repositories/" + label
            data = entry.get("data_path", "")
            if not isinstance(dest, str) or not isinstance(data, str):
                continue
            try:
                repo = (root / dest).resolve()
                folder = (repo / data).resolve()
            except (ValueError, RuntimeError, OSError):
                continue
            if repo in seen or not repo.is_relative_to(root) or not folder.is_relative_to(root):
                continue
            # A disabled user entry does not register a reader, so an enabled
            # default for that destination can still supply its database.
            if entry.get("should_read") is False:
                yield repo, None
            else:
                seen.add(repo)
                yield repo, None if entry.get("is_language") else folder


def _zip_has_cards(path: Path) -> bool:
    # The client can mount expansion ZIPs. Read only the directory, never extract a member.
    try:
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            # ZipFile materializes every central directory entry. Inspect the standard
            # end record first so giant art archives cannot exhaust discovery's budget.
            tail_size = min(22 + 65535, stream.seek(0, os.SEEK_END))
            stream.seek(-tail_size, os.SEEK_END)
            tail = stream.read(tail_size)
            end = tail.rfind(b"PK\x05\x06")
            if end < 0 or len(tail) - end < 22:
                return False
            entries, directory_size = struct.unpack_from("<HI", tail, end + 10)
            if entries > 8_192 or directory_size > 2 * 1024**2:
                return False
            with zipfile.ZipFile(stream) as archive:
                return any(
                    m.file_size and m.filename.lower().endswith(".cdb") and m.filename.count("/") <= 3
                    for m in archive.infolist()
                )
    except (OSError, ValueError, NotImplementedError, zipfile.BadZipFile):
        return False


def has_card_databases(root: Path) -> bool:
    """Stop at the first non-empty DB. Skip symlinked subtrees and bound fallback directory walks."""
    if _nonempty(root / "cards.cdb"):
        return True
    configured = set()
    deadline = monotonic() + 0.10
    # Scan loose expansions first, then configured database locations. Old installs can
    # have repository deltas without configs.json, including the DeltaBagooska releases.
    expansion = root / "expansions"
    repos = root / "repositories"
    folders = [(repos, "legacy", 0), (expansion, "expansion", 0)]
    for repo, data in _configured_repos(root, deadline):
        configured.add(repo)
        if data:
            folders.insert(1, (data, "configured", 0))
    while folders and monotonic() < deadline:
        folder, source, depth = folders.pop()
        if source == "legacy" and folder in configured:
            continue
        try:
            with os.scandir(folder) as entries:
                for item in entries:
                    if monotonic() >= deadline:
                        return False
                    name = item.name.lower()
                    if name.endswith(".cdb"):
                        eligible = (
                            source != "legacy"
                            or name.endswith(".delta.cdb")
                            or (folder == repos / "delta-bagooska" and name.startswith(("release-", "prerelease-")))
                        )
                        if eligible and _nonempty(Path(item.path)):
                            return True
                    elif name.endswith(".zip") and source == "expansion":
                        if _zip_has_cards(Path(item.path)):
                            return True
                    elif not name.startswith(".") and (source == "legacy" or (source == "expansion" and depth < 2)):
                        if item.is_dir(follow_symlinks=False):
                            folders.append((Path(item.path), source, depth + 1))
        except OSError:
            continue
    return False


def inspect_bundle(bundle: Path, *, root: Path | None = None) -> Install | None:
    """Resolve the bundle's declared executable and the data folder the game will use."""
    try:
        bundle = bundle.resolve()
        if bundle.suffix.lower() != ".app":
            return None
        contents = bundle / "Contents"
        values = plistlib.loads(_metadata(contents / "Info.plist"))
        name = values.get("CFBundleExecutable") if isinstance(values, dict) else None
        if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
            return None
        identity = f"{bundle.stem} {name} {values.get('CFBundleIdentifier', '')}".lower().replace(" ", "")
        if not any(token in identity for token in ("edopro", "projectignis", "ygopro")):
            return None
        executable = (contents / "MacOS" / name).resolve()
        if not executable.is_file():
            return None
        resources = contents / "Resources"
        bundled_roots = (resources / "ProjectIgnis", resources, contents / "MacOS")
        roots = (root.resolve(),) if root else (*bundled_roots, bundle.parent)
        data = None
        ready = False
        for candidate in roots:
            if not candidate.is_dir():
                continue
            if has_card_databases(candidate):
                data, ready = candidate, True
                break
            if data is None and (candidate / "config/system.conf").is_file():
                data = candidate
        if data is None:
            # A fresh bundle can bootstrap its databases. Never bootstrap into
            # /Applications just because it contains the game bundle.
            fallback = next((candidate for candidate in bundled_roots if candidate.is_dir()), None)
            if fallback is None or (root and root.resolve() != fallback.resolve()):
                return None
            data = fallback
        data = data.resolve()
        target = LaunchTarget(("/usr/bin/open", "-a", str(bundle), "--args", "-C", str(data)), data, description="macOS app")
        return Install(
            data,
            "macos-app",
            _plist_version(values),
            target,
            bundle.stem,
            ready,
            {"executable": str(executable), "bundle": str(bundle)},
        )
    except (OSError, RuntimeError, ValueError, ExpatError, plistlib.InvalidFileException):
        pass
    return None


def inspect_root(root: Path, *, executable: Path | None = None, bundle: Path | None = None) -> Install | None:
    """Build a launch target only for an identified game and its associated data root."""
    try:
        root = root.expanduser()
        if bundle:
            return inspect_bundle(bundle, root=root)
        if sys.platform == "darwin":
            root = root.resolve()
            if root.suffix.lower() == ".app":
                return inspect_bundle(root)
            for parent in root.parents[:3]:
                if parent.suffix.lower() == ".app":
                    if match := inspect_bundle(parent, root=root):
                        return match
                    return Install(root, label=root.name) if has_card_databases(root) else None
        # One readdir eliminates most unrelated candidates. If the directory is
        # enormous, switch to fixed-name lookups instead of walking all of it.
        try:
            with os.scandir(root) as entries:
                listed = {entry.name for entry in islice(entries, 129)}
        except PermissionError:
            listed = None  # A searchable directory need not be listable.
        complete = listed is not None and len(listed) < 129
        names = _WINDOWS_NAMES if sys.platform == "win32" else _NATIVE_NAMES + _WINDOWS_NAMES
        if complete:
            clients = [n for n in sorted(listed) if n.casefold() in _CLIENT_NAMES]
            if sys.platform == "win32":
                clients = [n for n in clients if n.lower().endswith(".exe")]
            names = tuple(dict.fromkeys([*(n for n in names if n in listed), *clients]))
        has_data_layout = not complete or bool(_DATA_MARKERS.intersection(name.lower() for name in listed))
        if executable is None and not names and not has_data_layout:
            return None
        root = root.resolve()
        ready = has_data_layout and has_card_databases(root)
        if sys.platform == "darwin" and (ready or (root / "config/system.conf").is_file()):
            # The data can live beside a renamed bundle. Reassociate it when
            # restoring a selection saved as the canonical data path alone.
            bundles = ["EDOPro.app", "ProjectIgnis.app"]
            if listed:
                bundles.extend(sorted(name for name in listed if name.lower().endswith(".app")))
            for bundle_name in dict.fromkeys(bundles):
                if match := inspect_bundle(root / bundle_name, root=root):
                    return match
        if executable is None:
            executable = next((root / n for n in names if (root / n).is_file()), None)
        if executable is None or not is_game_executable(executable) or not executable.is_file():
            return Install(root, label=root.name, ready=True) if ready else None
        if executable.parent.resolve() != root and not ready and not (root / "config/system.conf").is_file():
            return None
        windows_name = executable.suffix.lower() == ".exe"
        executable = executable.resolve()
        extra = {"executable": str(executable)}
        is_windows = windows_name or executable.suffix.lower() == ".exe"
        kind: Kind = "windows" if is_windows else "native"
        target = None
        if is_windows and sys.platform != "win32":
            kind = "proton"
            if drive := next((p for p in executable.parents if p.name.lower() == "drive_c"), None):
                extra["wineprefix"] = str(drive.parent)
        elif is_windows or (sys.platform != "win32" and os.access(executable, os.X_OK)):
            target = LaunchTarget(
                (str(executable), "-C", str(root)), root, description="Windows" if is_windows else "Native"
            )
        return Install(root, kind, game_version(executable), target, root.name, ready, extra)
    except (OSError, ValueError, RuntimeError):
        return None

"""Bounded installation discovery. Launcher records belong to the canonical data root."""

from __future__ import annotations

import configparser
import os
import re
import shlex
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path

from edopal.core.gameinfo import inspect_bundle, inspect_root, is_game_executable
from edopal.core.install import Install, LaunchTarget

_GAME_NAMES = ("ProjectIgnis", "EDOPro", "ProjectIgnis-EDOPro", "EDOPro-ProjectIgnis", "edopro", "projectignis")
_BUDGET = 1.65
_MOUNTS = (Path("/run/media"), Path("/media"), Path("/mnt"))
_STEAM_FLATPAK = "com.valvesoftware.Steam"
_LIBRARY_PAIR = re.compile(r'"(?:path|\d+)"\s*"((?:\\.|[^"\\])*)"', re.I)


def _game_name(value: str) -> bool:
    compact = re.sub(r"[\s._-]", "", value).casefold()
    return "edopro" in compact or "projectignis" in compact or "ygopro" in compact


def _children(path: Path, limit: int = 512, *, files: bool = False) -> Iterator[Path]:
    with suppress(OSError), os.scandir(path) as entries:
        for item in islice(entries, limit):
            with suppress(OSError):
                selected = item.is_file() if files else item.is_dir()
                if selected:
                    yield Path(item.path)


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _identity(path: Path) -> tuple[int, int] | Path:
    # APFS can accept several case spellings for one directory, including after resolve().
    info = path.stat()
    return (info.st_dev, info.st_ino) if info.st_ino else path.resolve()


def _read(path: Path, limit: int) -> bytes:
    if path.is_file() and path.stat().st_size <= limit:
        with path.open("rb") as file:
            data = file.read(limit + 1)
        if len(data) <= limit:
            return data
    return b""


def _binary_vdf(data: bytes) -> dict:
    """Read Valve's nested binary KeyValues as used by shortcuts.vdf (case-insensitive keys)."""
    stack = [{}]
    offset = 0
    while offset < len(data):
        kind = data[offset]
        offset += 1
        if kind == 8:  # end of object
            if len(stack) == 1:
                if offset != len(data):
                    raise ValueError("trailing binary KeyValues")
                return stack[0]
            stack.pop()
            continue
        end = data.index(b"\0", offset)
        key = data[offset:end].decode("utf-8", "replace").casefold()
        offset = end + 1
        if kind == 0:  # nested object, including each shortcut and its tags
            if len(stack) >= 16:
                raise ValueError("too many nested KeyValues")
            child = {}
            stack[-1][key] = child
            stack.append(child)
        elif kind == 1:
            end = data.index(b"\0", offset)
            stack[-1][key] = os.fsdecode(data[offset:end])
            offset = end + 1
        elif kind in (2, 3, 4, 6, 7):
            size = 8 if kind == 7 else 4
            if offset + size > len(data):
                raise ValueError("truncated binary KeyValue")
            if kind == 2:  # little-endian uint32, including appid with the high bit set
                stack[-1][key] = int.from_bytes(data[offset : offset + size], "little")
            offset += size
        else:
            raise ValueError("unknown binary KeyValue type")
    if len(stack) != 1:
        raise ValueError("unterminated binary KeyValues")
    return stack[0]


def _path(value: str, base: Path | None = None) -> Path | None:
    if not isinstance(value, str) or not (value := value.strip().strip('"')) or "\0" in value:
        return None
    try:
        result = Path(value).expanduser()
    except RuntimeError:
        return None  # A launcher can refer to a user account that no longer exists.
    if result.is_absolute():
        return result
    return base / result if base else None


@dataclass(frozen=True)
class _Candidate:
    path: Path
    executable: Path | None = None
    steam_root: Path | None = None
    appid: int = 0
    flatpak: str = ""
    bundle: Path | None = None

    def inspect(self) -> Install | None:
        install = inspect_root(self.path, executable=self.executable, bundle=self.bundle)
        if not install:
            return None
        if self.steam_root is not None:
            uri = f"steam://rungameid/{(self.appid << 32) | 0x02000000}"
            flatpak_steam = bool(self.flatpak)
            description = "Flatpak Steam" if flatpak_steam else "Steam"
            return replace(
                install,
                kind="steam-shortcut",
                label=f"{install.root.name} ({description})",
                launch=LaunchTarget(cwd=install.root, uri=uri, description=description),
                extra={**install.extra, "steam_root": str(self.steam_root), "flatpak_steam": flatpak_steam},
            )
        if self.flatpak:
            target = LaunchTarget(
                ("flatpak", "run", self.flatpak, "-C", str(install.root)), install.root, description="Flatpak"
            )
            return replace(install, kind="flatpak", launch=target, label=f"{install.root.name} (Flatpak)")
        return install


def _steam_clients(home: Path, data: Path) -> Iterator[tuple[Path, bool]]:
    if sys.platform == "darwin":
        paths = [home / "Library/Application Support/Steam"]
    elif sys.platform == "win32":
        paths = [
            Path(os.environ[k]) / "Steam" for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA") if os.getenv(k)
        ]
        import winreg

        with suppress(OSError), winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            if value := _path(winreg.QueryValueEx(key, "SteamPath")[0]):
                paths.insert(0, value)
    else:
        flatpak = home / ".var/app" / _STEAM_FLATPAK
        paths = [
            data / "Steam",
            home / ".local/share/Steam",
            home / ".steam/steam",
            home / ".steam/root",
            flatpak / ".local/share/Steam",
            flatpak / "data/Steam",
            flatpak / ".steam/steam",
        ]
    seen = set()
    for path in paths:
        with suppress(OSError, RuntimeError):
            if path.is_dir() and (canonical := path.resolve()) not in seen:
                seen.add(canonical)
                yield canonical, _STEAM_FLATPAK in canonical.parts or _STEAM_FLATPAK in path.parts


def _shortcut(record: dict, steam: Path, flatpak: bool) -> _Candidate | None:
    appid = record.get("appid")
    if not isinstance(appid, int) or not appid:
        return None
    start = _path(record.get("startdir", ""))
    exe = _path(record.get("exe", ""), base=start)
    if not exe:
        return None
    data = start
    options = record.get("launchoptions", "")
    tokens = shlex.split(options, posix=sys.platform != "win32") if isinstance(options, str) else []
    flags = [index for index, token in enumerate(tokens) if token.strip('"') == "-C"]
    if flags:
        if len(flags) != 1 or flags[0] + 1 >= len(tokens):
            return None
        data = _path(tokens[flags[0] + 1], base=start)
        if not data:
            return None
    # Steam on macOS accepts both a Finder bundle and the executable declared in its plist.
    bundle = None
    if sys.platform == "darwin":
        bundle = next((part for part in (exe, *exe.parents) if part.suffix.lower() == ".app"), None)
    if bundle:
        game = inspect_bundle(bundle, root=data)
        if not game and not flags:
            game = inspect_bundle(bundle)
        if not game:
            return None
        client = Path(game.extra["executable"])
        if exe != bundle and exe.resolve() != client:
            return None
        data, exe = game.root, client
    elif not is_game_executable(exe) or not exe.is_file():
        return None
    return _Candidate(
        data or exe.parent,
        exe,
        steam_root=steam,
        appid=appid,
        flatpak=_STEAM_FLATPAK if flatpak else "",
        bundle=bundle,
    )


def _shortcuts(steam: Path, flatpak: bool, deadline: float) -> Iterator[_Candidate]:
    for user in _children(steam / "userdata", 256):
        if time.monotonic() >= deadline:
            return
        with suppress(OSError, ValueError):
            table = _binary_vdf(_read(user / "config/shortcuts.vdf", 4 * 1024 * 1024)).get("shortcuts")
            if not isinstance(table, dict):
                continue
            for record in table.values():
                # Names can be anything. Match the actual target executable and data root.
                with suppress(OSError, ValueError, RuntimeError):
                    if isinstance(record, dict) and (candidate := _shortcut(record, steam, flatpak)):
                        yield candidate


def _steamapps(client: Path) -> Iterator[Path]:
    yield client / "steamapps"
    for file in (client / "steamapps/libraryfolders.vdf", client / "config/libraryfolders.vdf"):
        with suppress(OSError):
            raw = os.fsdecode(_read(file, 1024 * 1024))
            for match in _LIBRARY_PAIR.finditer(raw):
                if path := _path(re.sub(r'\\(["\\])', r"\1", match[1])):
                    yield path / "steamapps"


def _desktop_entries(data: Path, deadline: float) -> Iterator[_Candidate]:
    roots = [data, *map(Path, os.getenv("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"))]
    for root in dict.fromkeys(roots):
        if not root.is_absolute():
            continue
        for path in _children(root / "applications", files=True):
            if time.monotonic() >= deadline:
                return
            if path.suffix != ".desktop":
                continue
            with suppress(OSError, UnicodeError, ValueError, configparser.Error):
                content = _read(path, 64 * 1024).decode("utf-8-sig")
                if not _game_name(path.stem) and not _game_name(content):
                    continue
                entry = configparser.ConfigParser(interpolation=None, strict=False)
                entry.read_string(content)
                if not entry.has_section("Desktop Entry"):
                    continue
                group = entry["Desktop Entry"]
                root_path = _path(group.get("Path", ""))
                argv = shlex.split(group.get("Exec", ""))
                exe = _path(argv[0], base=root_path) if argv else None
                if exe and is_game_executable(exe) and exe.is_file():
                    if "-C" in argv and (i := argv.index("-C") + 1) < len(argv):
                        root_path = _path(argv[i], base=root_path) or root_path
                    yield _Candidate(root_path or exe.parent, exe)
                elif root_path and (_game_name(path.stem) or _game_name(group.get("Name", ""))):
                    yield _Candidate(root_path)


def _registry_roots(deadline: float) -> Iterator[Path]:
    """Called on Windows only. One inaccessible key does not hide other installations."""
    import winreg

    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

    def value(key, name: str) -> str:
        try:
            result = winreg.QueryValueEx(key, name)[0]
            return os.path.expandvars(result) if isinstance(result, str) else ""
        except OSError:
            return ""

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            with suppress(OSError), winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as parent:
                for index in range(4096):
                    if time.monotonic() >= deadline:
                        return
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    with suppress(OSError), winreg.OpenKey(parent, name) as key:
                        if not (_game_name(name) or _game_name(value(key, "DisplayName"))):
                            continue
                        if location := _path(value(key, "InstallLocation")):
                            yield location
                        icon = re.sub(r",\s*-?\d+\s*$", "", value(key, "DisplayIcon"))
                        if icon_path := _path(icon):
                            yield icon_path.parent


def _flatpak_games(home: Path, data: Path) -> Iterator[_Candidate]:
    for sandbox in _children(home / ".var/app"):
        appid = sandbox.name
        if not _game_name(appid) or not re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*){2,}", appid, re.ASCII):
            continue
        # A leftover data folder outlives an uninstalled Flatpak. Require an active deployment.
        bases = (data / "flatpak", Path("/var/lib/flatpak"), Path("/run/host/var/lib/flatpak"))
        active = False
        for base in bases:
            with suppress(OSError, UnicodeError, configparser.Error):
                metadata = _read(base / "app" / appid / "current/active/metadata", 256 * 1024)
                manifest = configparser.ConfigParser(interpolation=None, strict=False)
                manifest.read_string(metadata.decode("utf-8"))
                if manifest.get("Application", "name", fallback="") == appid:
                    active = True
                    break
        for base in (sandbox / "data", sandbox / ".local/share", sandbox / "config", sandbox):
            for path in (base, *_children(base, 48)):
                yield _Candidate(path, flatpak=appid if active else "")


def _portable_dirs() -> Iterator[Path]:
    for value in (os.environ.get("APPIMAGE"), sys.executable, sys.argv[0] if sys.argv else None):
        if value and (binary := _path(value)):
            directory = binary.parent
            yield directory
            if directory.name == "MacOS" and directory.parent.name == "Contents":
                yield directory.parents[2]
            else:
                yield directory.parent


def _volumes(home: Path) -> Iterator[Path]:
    for mount in _MOUNTS:
        for parent in (mount / home.name, mount):
            if _is_dir(parent):
                yield parent
                yield from _children(parent, 48)


def _mac_support(home: Path, deadline: float) -> Iterator[_Candidate]:
    # Pair a conventional writable Application Support folder only with a unique client.
    # Multiple clients or multiple data roots require an explicit registration (e.g. Steam).
    roots = [
        path
        for path in _children(home / "Library/Application Support")
        if path.name.casefold() in {"edopro", "projectignis"}
    ]
    if len(roots) != 1:
        return
    root = roots[0]
    matches = set()
    for apps in (home / "Applications", Path("/Applications")):
        for bundle in _children(apps):
            if time.monotonic() >= deadline:
                return
            if bundle.suffix.lower() != ".app" or not inspect_bundle(bundle, root=root):
                continue
            default = inspect_bundle(bundle)
            if default and default.root != root and (default.ready or (default.root / "config/system.conf").is_file()):
                continue  # This client already has another data folder of its own.
            matches.add(bundle.resolve())
            if len(matches) > 1:
                return
    if matches:
        yield _Candidate(root, bundle=matches.pop())


def _data_home(home: Path) -> Path:
    return _path(os.getenv("XDG_DATA_HOME", "")) or home / ".local/share"


def _registrations(home: Path, data: Path, clients: list[tuple[Path, bool]], deadline: float) -> Iterator[_Candidate]:
    for steam, flatpak in clients:
        if time.monotonic() >= deadline:
            return
        yield from _shortcuts(steam, flatpak, deadline)
    if sys.platform.startswith("linux") and time.monotonic() < deadline:
        yield from _flatpak_games(home, data)
        yield from _desktop_entries(data, deadline)
    elif sys.platform == "darwin":
        yield from _mac_support(home, deadline)


def _platform_candidates(hints: Iterable[Path], deadline: float) -> Iterator[_Candidate]:
    home = Path.home()
    data = _data_home(home)
    clients = list(_steam_clients(home, data))
    # Launch associations take precedence over plain folder sightings, including saved hints.
    yield from _registrations(home, data, clients, deadline)
    for hint in hints:
        yield _Candidate(Path(hint))
    places = [home, home / "Games", home / "Desktop", home / "Documents", home / "Downloads", home / "Applications"]
    for directory in dict.fromkeys(_portable_dirs()):
        yield _Candidate(directory)
        places.append(directory)
    libraries = dict.fromkeys(lib for client, _ in clients for lib in _steamapps(client) if _is_dir(lib))
    if sys.platform == "win32":
        if drive := os.environ.get("SystemDrive"):
            yield _Candidate(Path(drive + os.sep) / "ProjectIgnis")
        for path in _registry_roots(deadline):
            yield _Candidate(path)
        for name in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            if value := os.getenv(name):
                places.extend((Path(value), Path(value) / "Programs"))
    elif sys.platform == "darwin":
        places.extend((Path("/Applications"), home / "Library/Application Support"))
    else:
        places.extend((data, home / ".local/share", Path("/opt")))
        for volume in dict.fromkeys(_volumes(home)):
            places.extend((volume, volume / "Games"))
            for lib in (volume / "steamapps", volume / "SteamLibrary/steamapps"):
                if _is_dir(lib):
                    libraries[lib] = None
    places.extend(lib / "common" for lib in libraries)
    # Try installer defaults across every location first, then allow renamed installations.
    places = list(dict.fromkeys(p for p in places if _is_dir(p)))
    for place in places:
        for name in _GAME_NAMES:
            yield _Candidate(place / name)
    for place in places:
        for path in _children(place):
            yield _Candidate(path)
    if sys.platform.startswith("linux"):
        prefixes = [_path(os.getenv("WINEPREFIX", "")) or home / ".wine", home / ".wine"]
        for parent in (
            home / "Games",
            data / "wineprefixes",
            data / "bottles/bottles",
            home / ".var/app/com.usebottles.bottles/data/bottles/bottles",
        ):
            prefixes.extend(_children(parent))
        for lib in libraries:
            prefixes.extend(app / "pfx" for app in _children(lib / "compatdata", 1024))
        for prefix in dict.fromkeys(prefixes):
            if time.monotonic() >= deadline:
                return
            for dirname in ("", "Games", "Program Files", "Program Files (x86)"):
                for path in _children(prefix / "drive_c" / dirname, 128):
                    # Most directories repeat in every freshly-created Wine prefix.
                    # Skip its system skeleton and still inspect arbitrarily named games.
                    if path.name.casefold() not in {
                        "program files",
                        "program files (x86)",
                        "programdata",
                        "users",
                        "windows",
                        "games",
                        "common files",
                        "internet explorer",
                        "windows nt",
                        "windows media player",
                    }:
                        yield _Candidate(path)


def inspect(path: Path) -> Install:
    """Validate a manually chosen local path; discovery supplies registered launch associations."""
    try:
        path = Path(path).expanduser().resolve()
        if is_game_executable(path) and path.is_file():
            candidate = _Candidate(path.parent, path)
        else:
            candidate = _Candidate(path)
        if result := candidate.inspect():
            return result
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot inspect EDOPro installation at {path}") from exc
    raise ValueError(f"No EDOPro installation found at {path}")


def inspect_selected(path: Path) -> Install:
    """Resolve registrations for one selection without walking other game folders or prefixes."""
    try:
        wanted = Path(path).expanduser().resolve()
        if not wanted.exists():
            raise ValueError(f"EDOPro installation no longer exists at {path}")
        identity = _identity(wanted)
        home = Path.home()
        data = _data_home(home)
        clients = list(_steam_clients(home, data))
        deadline = time.monotonic() + _BUDGET
        for candidate in _registrations(home, data, clients, deadline):
            if time.monotonic() >= deadline:
                break
            with suppress(OSError, RuntimeError, ValueError):
                if any(p and _identity(p) == identity for p in (candidate.path, candidate.executable, candidate.bundle)):
                    if result := candidate.inspect():
                        return result
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot inspect EDOPro installation at {path}") from exc
    return inspect(path)


def discover(hints: Iterable[Path] = ()) -> list[Install]:
    """Return one entry per canonical data root. No filesystem crawling or executable probes."""
    deadline = time.monotonic() + _BUDGET
    checked = set()
    found = {}
    associated_bundles = set()
    for candidate in _platform_candidates(hints, deadline):
        if time.monotonic() >= deadline:
            break
        with suppress(OSError, ValueError, RuntimeError):
            if candidate in checked or not candidate.path.is_dir():
                continue
            checked.add(candidate)
            if _identity(candidate.path) in found:
                continue
            result = candidate.inspect()
            if not result:
                continue
            identity = _identity(result.root)
            if identity in found:
                continue
            if (
                candidate.path in associated_bundles
                and not result.ready
                and not (result.root / "config/system.conf").is_file()
            ):
                continue  # This bundle already has a registered data folder.
            found[identity] = result
            if candidate.bundle:
                associated_bundles.add(candidate.bundle)
    return sorted(found.values(), key=lambda i: (not i.ready, str(i.root).casefold()))

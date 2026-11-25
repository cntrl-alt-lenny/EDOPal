"""Read-only discovery exercises using synthetic Steam clients and platform registrations."""

import os
import plistlib
import struct
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from edopal.core import discovery

APPID = 0xBD325358


def put(path, content=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def binary_object(values):
    data = bytearray()
    for key, value in values.items():
        name = key.encode() + b"\0"
        if isinstance(value, dict):
            data += b"\x00" + name + binary_object(value)
        elif isinstance(value, int):
            data += b"\x02" + name + struct.pack("<I", value)
        else:
            data += b"\x01" + name + os.fsencode(value) + b"\0"
    return bytes(data) + b"\x08"


def register(steam, exe, start, appid=APPID, options=""):
    record = {"AppName": "Custom title", "Exe": f'"{exe}"', "StartDir": f'"{start}"', "tags": {"0": "Games"}, "appid": appid}
    record["LaunchOptions"] = options
    return put(steam / "userdata/42/config/shortcuts.vdf", binary_object({"shortcuts": {"0": record}}))


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["edopal"])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "programs/bin/edopal"))
    monkeypatch.setattr(discovery, "_MOUNTS", (tmp_path / "media",))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "xdg-system"))
    for var in ("APPIMAGE", "WINEPREFIX", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA", "SystemDrive"):
        monkeypatch.delenv(var, raising=False)
    return home


def test_shortcut_matches_paths_and_preserves_split_install(home, tmp_path):
    data = home / "Games/ProjectIgnis"
    put(data / "cards.cdb", b"cards")
    exe = put(tmp_path / "installed binaries/EDOPro.exe")
    steam = home / ".local/share/Steam"
    register(steam, exe, data)
    alias = home / "Games/Alias"
    try:
        alias.symlink_to(data, target_is_directory=True)
    except OSError:
        alias = data
    installs = discovery.discover([alias])
    assert [i.root for i in installs] == [data]
    selected = discovery.inspect_selected(alias)
    assert selected == installs[0]
    assert selected.extra["steam_root"] == str(steam)
    assert selected.extra["executable"] == str(exe)
    assert selected.launch.uri == "steam://rungameid/13633050659459694592"
    assert selected.launch.cwd == data
    assert selected.kind == "steam-shortcut"
    assert selected.ready


def test_folder_with_multiple_spellings_keeps_one_steam_target(home, monkeypatch):
    root = home / "Games/ProjectIgnis"
    alias = home / "Games/projectignis"
    exe = put(root / "EDOPro.exe")
    put(alias / "cards.cdb", b"cards")
    register(home / ".local/share/Steam", exe, root)
    # Model a case-insensitive POSIX volume, where resolve() may keep both spellings.
    identity = discovery._identity
    monkeypatch.setattr(discovery, "_identity", lambda path: identity(root if path == alias else path))
    (install,) = discovery.discover([alias])
    assert install.root == root
    assert install.kind == "steam-shortcut"
    assert discovery.inspect_selected(alias).launch == install.launch


def test_broken_vdf_and_wrong_executable_leave_game_selectable(home, tmp_path):
    root = home / "Games/ProjectIgnis"
    put(root / "EDOPro").chmod(0o755)
    steam = home / ".local/share/Steam"
    register(steam, put(tmp_path / "a different game"), root)
    put(steam / "userdata/13/config/shortcuts.vdf", b"\x00shortcuts\0\x000\0\x02appid\0\x01")
    (game,) = discovery.discover()
    assert game.root == root
    assert not game.ready
    assert game.kind == "native"
    assert game.launch.argv == (str(root / "EDOPro"), "-C", str(root))


def test_data_roots_in_steam_libraries_prefixes_and_removable_media(home, tmp_path, monkeypatch):
    steam = home / ".local/share/Steam"
    modern, legacy = tmp_path / "Library with spaces", tmp_path / "old library"
    put(steam / "steamapps/libraryfolders.vdf", f'"libraryfolders" {{ "1" {{ "path" "{modern.as_posix()}" }} }}'.encode())
    put(steam / "config/libraryfolders.vdf", f'"LibraryFolders" {{ "2" "{legacy.as_posix()}" }}'.encode())
    prefix = tmp_path / "custom wineprefix"
    monkeypatch.setenv("WINEPREFIX", str(prefix))
    roots = [
        modern / "steamapps/common/Renamed",
        legacy / "steamapps/compatdata/999/pfx/drive_c/Program Files (x86)/ProjectIgnis",
        prefix / "drive_c/ProjectIgnis",
        tmp_path / "media/home/SD card/SteamLibrary/steamapps/common/alt",
        tmp_path / "media/drive/Games/Renamed again",
        tmp_path / "programs/bin/ProjectIgnis",
        home / ".local/share/ProjectIgnis",
    ]
    for root in roots:
        put(root / "cards.cdb", b"x")
    put(roots[1] / "EDOPro.exe")
    installs = {i.root: i for i in discovery.discover()}
    assert installs.keys() == set(roots)
    assert installs[roots[1]].kind == "proton"
    assert installs[roots[1]].launch is None


def test_desktop_file_discovery_preserves_explicit_working_directory(home, tmp_path):
    data = tmp_path / "different folder/My data"
    put(data / "config/system.conf", b"flag=1")
    exe = put(tmp_path / "binaries/EDOPro")
    exe.chmod(0o755)
    entry = f'[Desktop Entry]\nType=Application\nName=My game\nPath={tmp_path}\nExec="{exe}" -C "{data}"\n'
    put(home / ".local/share/applications/personally-named.desktop", entry.encode())
    game = discovery.inspect_selected(data)
    assert game.root == data
    assert game.launch.argv == (str(exe), "-C", str(data))
    assert discovery.discover() == [game]


def test_flatpak_shortcut_uses_owning_client(home, tmp_path):
    steam = home / ".var/app/com.valvesoftware.Steam/data/Steam"
    root = tmp_path / "outside any known location/EDOPro"
    exe = put(root / "EDOPro.exe")
    register(steam, exe, root)
    (install,) = discovery.discover()
    assert install.kind == "steam-shortcut"
    assert install.extra["flatpak_steam"] is True
    assert install.extra["steam_root"] == str(steam)
    assert discovery.inspect_selected(root).launch.uri == install.launch.uri


def test_flatpak_requires_an_active_installed_id(home):
    appid = "example.test.EDOPro"
    data = home / ".var/app" / appid / "data/ProjectIgnis"
    put(data / "cards.cdb", b"x")
    (uninstalled,) = discovery.discover()
    assert uninstalled.launch is None
    put(home / ".local/share/flatpak/app" / appid / "current/active/metadata", f"[Application]\nname={appid}\n".encode())
    (installed,) = discovery.discover()
    assert installed.launch.argv == ("flatpak", "run", appid, "-C", str(data))
    assert discovery.inspect_selected(data).kind == "flatpak"


def test_bundle_and_selected_data_share_one_launch_target(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    bundle = home / "Applications/EDOPro.app"
    data = bundle / "Contents/Resources/ProjectIgnis"
    put(data / "cards.cdb", b"cards")
    put(bundle / "Contents/MacOS/custom-client")
    put(
        bundle / "Contents/Info.plist",
        plistlib.dumps({"CFBundleExecutable": "custom-client", "CFBundleShortVersionString": "1.2"}),
    )
    (game,) = discovery.discover([data])
    assert game.root == data
    assert game.launch.argv == ("/usr/bin/open", "-a", str(bundle), "--args", "-C", str(data))
    assert discovery.inspect_selected(bundle) == discovery.inspect_selected(data) == game


def test_windows_uninstall_registry_views_and_system_drive(home, tmp_path, monkeypatch):
    drive = tmp_path / "system disk"
    base = drive / "ProjectIgnis"
    location = tmp_path / "elsewhere/custom1"
    icon = tmp_path / "elsewhere/custom2/EDOPro.exe"
    unrelated = tmp_path / "elsewhere/custom3"
    for path in (base, location, unrelated, icon.parent):
        put(path / "cards.cdb", b"x")
    put(icon)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemDrive", str(drive))
    values = {
        ("user", 1): [{"DisplayName": "Project Ignis EDOPro", "InstallLocation": str(location)}],
        ("machine", 2): [{"DisplayName": "EDOPro", "DisplayIcon": f'"{icon}",0'}],
        ("machine", 1): [{"DisplayName": "Unrelated", "InstallLocation": str(unrelated)}],
    }
    visited = []

    def open_key(parent, key, reserved=0, flags=0):
        if key == r"Software\Valve\Steam":
            raise OSError("no Steam")
        if isinstance(parent, str):
            visited.append((parent, flags))
            return nullcontext(values.get((parent, flags), []))
        return nullcontext(parent[int(key)])

    def enum_key(parent, index):
        if index >= len(parent):
            raise OSError("no more keys")
        return str(index)

    def query(parent, key):
        if key not in parent:
            raise OSError("missing value")
        return parent[key], 0

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="user",
        HKEY_LOCAL_MACHINE="machine",
        KEY_READ=0,
        KEY_WOW64_32KEY=1,
        KEY_WOW64_64KEY=2,
        OpenKey=open_key,
        EnumKey=enum_key,
        QueryValueEx=query,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    games = discovery.discover()
    assert {game.root for game in games} == {base, location, icon.parent}
    assert set(visited) == {("machine", 1), ("machine", 2), ("user", 1), ("user", 2)}
    assert discovery.inspect(icon.parent).launch.argv == (str(icon), "-C", str(icon.parent))


@pytest.mark.parametrize("use_bundle", [True, False])
def test_macos_steam_data_override_and_restore_from_bundle(home, tmp_path, monkeypatch, use_bundle):
    monkeypatch.setattr(sys, "platform", "darwin")
    bundle = home / "Applications/ProjectIgnis.app"
    data = tmp_path / "separate game data"
    put(data / "cards.cdb", b"cards")
    client = put(bundle / "Contents/MacOS/custom-client")
    put(bundle / "Contents/Info.plist", plistlib.dumps({"CFBundleExecutable": "custom-client"}))
    register(
        home / "Library/Application Support/Steam",
        bundle if use_bundle else client,
        bundle.parent,
        options=f'%command% -C "{data}"',
    )
    (game,) = discovery.discover()
    assert game.root == data
    assert game.kind == "steam-shortcut"
    assert game.extra["bundle"] == str(bundle)
    assert game.extra["executable"] == str(client)
    assert discovery.inspect_selected(bundle) == discovery.inspect_selected(data) == game


def test_macos_conventional_split_requires_a_unique_bundle(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    data = home / "Library/Application Support/ProjectIgnis"
    put(data / "cards.cdb", b"x")
    for name in ("EDOPro", "another EDOPro build"):
        bundle = home / "Applications" / f"{name}.app"
        put(bundle / "Contents/Info.plist", plistlib.dumps({"CFBundleExecutable": "custom-client"}))
        put(bundle / "Contents/MacOS/custom-client")
        if name == "EDOPro":
            game = discovery.inspect_selected(data)
            assert game.extra["bundle"] == str(bundle)
            assert game.launch.argv[-2:] == ("-C", str(data))
            assert discovery.discover() == [game]
        else:
            assert discovery.inspect_selected(data).launch is None


def test_linux_steam_explicit_data_root_wins_over_start_directory(home, tmp_path):
    client = put(tmp_path / "client/my copy/EDOPro.exe")
    data = tmp_path / "split storage/Tournament profile"
    put(data / "cards.cdb", b"x")
    register(home / ".local/share/Steam", client, client.parent, options=f'PROTON_LOG=1 %command% -C "{data}"')
    (item,) = discovery.discover()
    assert item.root == data
    assert item.launch.uri == "steam://rungameid/13633050659459694592"
    assert discovery.inspect_selected(data) == item
    assert discovery.inspect_selected(client.parent).launch is None

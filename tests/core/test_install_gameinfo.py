import json
import plistlib
import zipfile
from pathlib import Path

import pytest

from edopal.core import gameinfo

ELF = (b"\x7fELF\x02\x01\x01" + bytes(9) + b"\x03\x00").ljust(64, b"\0")
PE = b"MZ" + bytes(58) + (64).to_bytes(4, "little") + b"PE\x00\x00" + bytes(20)


@pytest.mark.parametrize(("name", "header"), [("EDOPro", ELF), ("EDOPro.exe", PE)])
def test_compiled_version_survives_chunk_boundary_and_game_update(tmp_path, monkeypatch, name, header):
    monkeypatch.setattr(gameinfo.sys, "platform", "linux")
    monkeypatch.setattr(gameinfo, "_CHUNK", 128)
    executable = tmp_path / name
    executable.write_bytes(header.ljust(120, b"z") + b"\x00EDOPro version 41.0.2\x00")
    assert gameinfo.game_version(executable) == "41.0.2"
    executable.write_bytes(header + b"\x00EDOPro version 42.1.0\x00")
    assert gameinfo.game_version(executable) == "42.1.0"


@pytest.mark.parametrize(
    "contents",
    [
        b"Not an executable" * 5,
        ELF + b"patch notes: EDOPro version 41.0.2\0",
        ELF + b"\x00EDOPro version 41.0.2\x00EDOPro version 42.1.0\x00",
        b"MZ" + bytes(90),
    ],
)
def test_unreliable_version_is_unknown(tmp_path, contents):
    executable = tmp_path / "EDOPro.exe"
    executable.write_bytes(contents)
    assert gameinfo.game_version(executable) is None


@pytest.mark.parametrize(
    "source",
    [
        "cards.cdb",
        "expansions/sets/a/set.cdb",
        "repositories/custom/deep/cards.delta.cdb",
        "repositories/delta-bagooska/prerelease-new.cdb",
    ],
)
def test_data_only_discovery_needs_nonempty_db(tmp_path, source):
    path = tmp_path / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    assert gameinfo.inspect_root(tmp_path) is None
    path.write_bytes(b"card database bytes")
    install = gameinfo.inspect_root(tmp_path)
    assert install.ready and install.launch is None
    assert install.root == tmp_path


def test_zip_and_configured_only_data(tmp_path):
    custom = tmp_path / "my-repo/loose/set.cdb"
    custom.parent.mkdir(parents=True)
    custom.write_bytes(b"1")
    config = tmp_path / "config/configs.json"
    config.parent.mkdir()
    repo = {"repo_path": "my-repo", "data_path": "loose"}
    config.write_text(json.dumps({"repos": [repo]}))
    assert gameinfo.inspect_root(tmp_path).ready
    for exclude in ({"is_language": True}, {"should_read": False}):
        config.write_text(json.dumps({"repos": [repo | exclude]}))
        assert gameinfo.inspect_root(tmp_path) is None
    pack = tmp_path / "expansions/set.zip"
    pack.parent.mkdir()
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("subfolder/cards.cdb", b"123")
    assert gameinfo.inspect_root(tmp_path).ready


def test_fresh_native_split_with_spaces_and_missing_execute_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(gameinfo.sys, "platform", "linux")
    exe = tmp_path / "installed elsewhere/EDOPro"
    exe.parent.mkdir()
    exe.write_bytes(ELF + b"\x00EDOPro version 41.0.2\x00")
    exe.chmod(0o755)
    assert not gameinfo.inspect_root(exe.parent).ready
    root = tmp_path / "data files"
    (root / "config").mkdir(parents=True)
    (root / "config/system.conf").touch()
    install = gameinfo.inspect_root(root, executable=exe)
    assert install.launch.argv == (str(exe), "-C", str(root))
    assert install.launch.cwd == root and not install.ready
    assert install.version == "41.0.2" and install.extra["executable"] == str(exe)
    exe.chmod(0o644)
    if not gameinfo.os.access(exe, gameinfo.os.X_OK):
        assert gameinfo.inspect_root(root, executable=exe).launch is None
    assert gameinfo.inspect_root(tmp_path, executable=exe) is None


def test_proton_and_windows_share_the_binary_but_need_the_right_launcher(tmp_path, monkeypatch):
    root = tmp_path / "wine prefix/drive_c/Program Files/ProjectIgnis"
    root.mkdir(parents=True)
    exe = root / "EDOPro.exe"
    exe.write_bytes(PE + b"\0EDOPro version 41.0.2\0")
    monkeypatch.setattr(gameinfo.sys, "platform", "linux")
    wine = gameinfo.inspect_root(root)
    assert wine.kind == "proton" and wine.launch is None
    assert wine.extra["wineprefix"] == str(tmp_path / "wine prefix")
    monkeypatch.setattr(gameinfo.sys, "platform", "win32")
    native = gameinfo.inspect_root(root)
    assert native.kind == "windows" and native.launch.argv == (str(exe), "-C", str(root))
    assert native.launch.cwd == root and native.version == "41.0.2"


def test_windows_client_link_is_never_launched_directly_from_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(gameinfo.sys, "platform", "linux")
    target = tmp_path / "client-data"
    target.write_bytes(PE)
    target.chmod(0o755)
    try:
        (tmp_path / "EDOPRO.EXE").symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("File symlinks unavailable")
    install = gameinfo.inspect_root(tmp_path)
    assert install.kind == "proton" and install.launch is None


def test_discovery_skips_db_probes_for_unrelated_small_directories(tmp_path, monkeypatch):
    (tmp_path / "unrelated file").touch()
    monkeypatch.setattr(gameinfo, "has_card_databases", lambda *_: pytest.fail("expensive DB inspection"))
    assert gameinfo.inspect_root(tmp_path) is None


def test_crowded_game_directory_keeps_fixed_name_fallback(tmp_path):
    for i in range(180):
        (tmp_path / str(i)).touch()
    (tmp_path / "cards.cdb").write_bytes(b"downloaded")
    assert gameinfo.inspect_root(tmp_path).ready


@pytest.mark.parametrize("rel", ["Contents/Resources", "Contents/Resources/ProjectIgnis", "Contents/MacOS", ".."])
def test_bundle_lookup_from_bundle_and_data_root(tmp_path, monkeypatch, rel):
    monkeypatch.setattr(gameinfo.sys, "platform", "darwin")
    bundle = tmp_path / "portable root/EDOPro.app"
    contents = bundle / "Contents"
    exe = contents / "MacOS/Named Build"
    exe.parent.mkdir(parents=True)
    exe.touch()
    plist = contents / "Info.plist"
    plist.write_bytes(
        plistlib.dumps(
            {"CFBundleExecutable": exe.name, "CFBundleShortVersionString": "41.0.2", "CFBundleVersion": "88"},
            fmt=plistlib.FMT_BINARY,
        )
    )
    fresh = gameinfo.inspect_bundle(bundle)
    assert fresh.root == exe.parent and not fresh.ready
    assert gameinfo.inspect_root(bundle.parent) is None
    data = (bundle / rel).resolve()
    (data / "config").mkdir(parents=True)
    (data / "config/system.conf").touch()
    for selected in (data, bundle):
        install = gameinfo.inspect_root(selected)
        assert install.kind == "macos-app" and install.root == data and not install.ready
        assert install.version == "41.0.2"
        assert install.extra == {"executable": str(exe), "bundle": str(bundle)}
        assert install.launch.argv == ("/usr/bin/open", "-a", str(bundle), "--args", "-C", str(data))
        assert install.launch.cwd == data
    plist.write_bytes(plistlib.dumps({"CFBundleExecutable": "../../other"}))
    assert gameinfo.inspect_root(bundle) is None


def test_bundle_db_has_priority_over_a_fresh_config_root(tmp_path):
    bundle = tmp_path / "EDOPro.app"
    resources = bundle / "Contents/Resources"
    (resources / "ProjectIgnis/config").mkdir(parents=True)
    (resources / "ProjectIgnis/config/system.conf").touch()
    exe = bundle / "Contents/MacOS/EDOPro"
    exe.parent.mkdir()
    exe.touch()
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": exe.name}))
    (resources / "cards.cdb").write_bytes(b"cards")
    assert gameinfo.inspect_bundle(bundle).root == resources


def test_restore_selected_data_root_beside_renamed_bundle(tmp_path, monkeypatch):
    from edopal.core.installs import RealInstalls
    from edopal.core.settings import Settings

    monkeypatch.setattr(gameinfo.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / "Portable Project"
    bundle = root / "Practice.app"
    exe = bundle / "Contents/MacOS/EDOPro"
    exe.parent.mkdir(parents=True)
    exe.touch()
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": exe.name}))
    (root / "cards.cdb").write_bytes(b"downloaded")
    settings = tmp_path / "settings.json"
    selected = RealInstalls(Settings(settings)).select(bundle)
    restored = RealInstalls(Settings(settings)).current()
    assert restored.root == selected.root == root
    assert restored.launch == selected.launch
    assert restored.extra["bundle"] == str(bundle)


def test_fixture_install_is_ready():
    root = Path(__file__).resolve().parents[1] / "fixtures/game"
    assert gameinfo.inspect_root(root).ready

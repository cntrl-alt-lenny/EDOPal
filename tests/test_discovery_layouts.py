from pathlib import Path

import pytest

import config
from card_databases import get_db_files


@pytest.mark.parametrize("kind", ["steam", "proton", "wine", "flatpak", "flatpak-steam"])
def test_linux_installation_layouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    home = tmp_path / "home"
    steam = home / (
        ".var/app/com.valvesoftware.Steam/.local/share/Steam"
        if kind == "flatpak-steam"
        else ".local/share/Steam"
    )
    apps = tmp_path / "SD Card/SteamLibrary/steamapps"
    if kind in ("steam", "proton", "flatpak-steam"):
        manifests = steam / "steamapps"
        manifests.mkdir(parents=True)
        (manifests / "libraryfolders.vdf").write_text(
            '"libraryfolders" { "0" { "path" "'
            + apps.parent.as_posix()
            + '" "apps" { "123" "456" } } }'
        )
    paths = {
        "steam": apps / "common/My card game",
        "flatpak-steam": apps / "common/My card game",
        "proton": apps / "compatdata/123/pfx/drive_c/Program Files (x86)/ProjectIgnis",
        "wine": tmp_path / "my-wine/drive_c/ProjectIgnis",
        "flatpak": home / ".var/app/org.example.EDOPro/data/ProjectIgnis",
    }
    target = paths[kind]
    target.mkdir(parents=True)
    (target / "cards.cdb").touch()
    monkeypatch.setenv("WINEPREFIX", str(tmp_path / "my-wine"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(config.os.path, "expanduser", lambda _: str(home))
    assert config.find_edopro_folder() == str(target)


def test_bundle_guess_resolves_validated_game_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "EDOPro.app"
    data = bundle / "Contents/Resources"
    data.mkdir(parents=True)
    (bundle / "Contents/Info.plist").touch()
    (data / "cards.cdb").touch()
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config, "_search_parents", lambda: [])
    assert config.find_edopro_folder([str(bundle)]) == str(data)


def test_bare_bundle_does_not_identify_applications_as_game_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    applications = tmp_path / "Applications"
    bundle = applications / "EDOPro.app"
    binaries = bundle / "Contents/MacOS"
    binaries.mkdir(parents=True)
    (binaries / "EDOPro").touch()
    (bundle / "Contents/Info.plist").touch()
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config, "_search_parents", lambda: [str(applications)])
    assert config.find_edopro_folder([str(bundle)]) is None
    assert not config.looks_like_edopro_folder(str(applications))
    assert not config.looks_like_edopro_folder(str(binaries))


def test_fresh_bundle_game_root_passes_installation_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "Applications/EDOPro.app"
    contents = bundle / "Contents"
    data = contents / "Resources/ProjectIgnis"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").touch()
    (contents / "MacOS/EDOPro").touch()
    (data / "config").mkdir(parents=True)
    (data / "config/system.conf").touch()
    (data / "sound/BGM").mkdir(parents=True)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config, "_search_parents", lambda: [str(bundle.parent)])
    assert config.find_edopro_folder() == str(data)
    assert config.looks_like_edopro_folder(str(data))
    assert not config.folder_has_card_databases(str(data))


@pytest.mark.parametrize("filename", ["release-betb.cdb", "prerelease-cards-rush.cdb"])
def test_release_database_is_discovered_and_scannable(tmp_path: Path, filename: str) -> None:
    other = tmp_path / "repositories/beta" / filename
    other.parent.mkdir(parents=True)
    other.touch()
    assert not config.folder_has_card_databases(str(tmp_path))
    assert get_db_files(str(tmp_path)) == []
    db = tmp_path / "repositories/delta-bagooska" / filename
    db.parent.mkdir(parents=True)
    db.touch()
    assert config.folder_has_card_databases(str(tmp_path))
    assert get_db_files(str(tmp_path)) == [str(db)]


def test_removable_library_without_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "deck"
    mounts = tmp_path / "mounts"
    target = mounts / "SD Card/SteamLibrary/steamapps/common/EDOPro"
    target.mkdir(parents=True)
    (target / "cards.cdb").touch()
    scan = config.os.scandir
    monkeypatch.setattr(
        config.os,
        "scandir",
        lambda path: scan(mounts if Path(path).as_posix() == "/run/media/deck" else path),
    )
    monkeypatch.setattr(config.os.path, "expanduser", lambda _: str(home))
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config.find_edopro_folder() == str(target)


def test_expired_search_does_not_walk_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "repositories/deep").mkdir(parents=True)
    monkeypatch.setattr(config, "_SEARCH_TIME_BUDGET_SECONDS", 0)
    monkeypatch.setattr(config.os, "scandir", lambda *_: pytest.fail("scan started after deadline"))
    assert config.find_edopro_folder([str(tmp_path)]) is None

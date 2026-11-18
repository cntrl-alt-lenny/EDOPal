from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

import extras
import music_library
from state_lock import appearance_lock
from themes import (
    PRESETS,
    install_state,
    install_theme,
    recover,
    remove_theme,
    scale_background,
    switch_back,
    undo_theme,
)
from themes.transaction import MARKER, STATE

ID = "abc1" * 8
OTHER = "b2" * 16
THEME = replace(PRESETS[0], wallpaper="inherit")
ORIGINAL = b'\xef\xbb\xbfskin\t= Grey\r\n# \xff\r\nvolume = 41\r\nfallbackFonts = "bundled 12"\r\nnumfont = own.ttf'


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "Game"
    conf = root / "config/system.conf"
    conf.parent.mkdir(parents=True)
    conf.write_bytes(ORIGINAL)
    return root


def test_install_edit_back_undo_remove_round_trip(game: Path) -> None:
    foreign = game / "skin" / ("Studio-" + ID)
    foreign.mkdir(parents=True)
    (foreign / "skin.xml").write_bytes(b"private")
    theme = replace(THEME, wallpaper="gradient", font="inter")
    name = install_theme(game, ID, theme)
    assert name != foreign.name
    folder = game / "skin" / name
    conf = game / "config/system.conf"
    assert extras.current_skin(str(game)) == name
    assert (folder / "fonts/Inter-OFL.txt").is_file()
    assert (folder / "fonts/InterEDO-Regular.ttf").is_file()
    installed = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    wallpaper = folder / "textures/bg.png"
    assert QImage(str(wallpaper)).size().toTuple() == (2560, 1600)
    other = folder / "textures/cover.jpg"
    other.write_bytes(b"player sleeve")
    updated = replace(THEME, name="Renamed", roundness=2, accent="#9051ce")
    assert install_theme(game, ID, updated) == name
    assert not wallpaper.exists() and not (folder / "fonts").exists()
    assert b"numfont = own.ttf" in conf.read_bytes()
    assert b"textfont" not in conf.read_bytes()
    assert other.read_bytes() == b"player sleeve"
    # Repeat is a no-op and retains the undo point.
    install_theme(game, ID, updated)
    conf.write_bytes(conf.read_bytes().replace(b"volume = 41", b"volume = 73"))
    undo_theme(game, ID)
    assert all((folder / k).read_bytes() == v for k, v in installed.items())
    assert other.read_bytes() == b"player sleeve"
    switch_back(game, ID)
    assert wallpaper.is_file() and (folder / MARKER).is_file()
    assert conf.read_bytes() == ORIGINAL.replace(b"41", b"73")
    remove_theme(game, ID)
    assert list(folder.rglob("*")) == [folder / "textures", other]
    assert (foreign / "skin.xml").read_bytes() == b"private"
    assert conf.read_bytes() == ORIGINAL.replace(b"41", b"73")


def test_unowned_files_and_modified_owned_files_are_preserved(game: Path) -> None:
    name = install_theme(game, ID, THEME)
    folder = game / "skin" / name
    stale = folder / "textures/bg.png"
    stale.parent.mkdir()
    stale.write_bytes(b"player wallpaper")
    with pytest.raises(ValueError, match="not owned"):
        install_theme(game, ID, replace(THEME, wallpaper="geometry"))
    assert stale.read_bytes() == b"player wallpaper"
    edited = folder / "Window.png"
    edited.write_bytes(b"hand-edited")
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    for operation in (remove_theme, undo_theme):
        with pytest.raises(ValueError, match="changed outside"):
            operation(game, ID)
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
    switch_back(game, ID)
    assert extras.current_skin(str(game)) == "Grey"


@pytest.mark.parametrize("stop", ["ButtonDisabled.png", "system.conf", "record.json"])
def test_interrupted_install_resumes_and_undo_removes_only_owned(
    game: Path, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    original_write = extras._atomic_write

    def stop_at(path: Path, data: bytes) -> None:
        if path.name == stop:
            raise KeyboardInterrupt
        original_write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(extras, "_atomic_write", stop_at)
        with pytest.raises(KeyboardInterrupt):
            install_theme(game, ID, THEME)
    assert (game / STATE / "pending.json").is_file()
    assert extras.current_skin(str(game)) == ("Studio-" + ID if stop == "record.json" else "Grey")
    conf = game / "config/system.conf"
    conf.write_bytes(conf.read_bytes() + b"\r\n# during recovery")
    recover(game)
    recover(game)
    folder = game / "skin" / extras.current_skin(str(game))
    assert folder.name == "Studio-" + ID
    assert (folder / "Window.png").is_file()
    undo_theme(game, ID)
    assert not folder.exists()
    assert conf.read_bytes() == ORIGINAL + b"\r\n# during recovery"


@pytest.mark.parametrize("operation", [undo_theme, remove_theme])
def test_interrupted_deletion_resumes(
    game: Path, monkeypatch: pytest.MonkeyPatch, operation: Callable[[Path, str], None]
) -> None:
    name = install_theme(game, ID, THEME)
    original_unlink = Path.unlink

    def interrupt(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "Window.png":
            raise KeyboardInterrupt
        original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(KeyboardInterrupt):
            operation(game, ID)
    assert extras.current_skin(str(game)) == "Grey"
    operation(game, ID)
    assert not (game / "skin" / name).exists()
    assert not (game / STATE / "pending.json").exists()


def test_predecessor_removed_preserves_external_font_and_unrelated_skin(game: Path) -> None:
    conf = game / "config/system.conf"
    a = install_theme(game, ID, replace(THEME, font="inter"))
    b = install_theme(game, OTHER, replace(THEME, name="Second"))
    assert b != a
    assert a.encode() in conf.read_bytes()  # B inherited A's font settings.
    conf.write_bytes(
        conf.read_bytes().replace(
            f"skin/{a}/fonts/InterEDO-Regular.ttf 12".encode(), b"custom.ttf 14"
        )
    )
    remove_theme(game, ID)
    assert extras.current_skin(str(game)) == b
    assert b"custom.ttf 14" in conf.read_bytes() and b"numfont = own.ttf" in conf.read_bytes()
    switch_back(game, OTHER)
    assert extras.current_skin(str(game)) == "Grey"
    conf.write_bytes(conf.read_bytes().replace(b"Grey", b"Later"))
    remove_theme(game, OTHER)
    assert extras.current_skin(str(game)) == "Later"


def test_all_config_writers_observe_one_install_lock(
    game: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = str(tmp_path / "separate-config/config.json")
    monkeypatch.setattr(music_library, "_game_running", lambda path: False)
    with ThreadPoolExecutor(max_workers=1) as pool, appearance_lock(game):
        for run in (
            lambda: install_theme(game, ID, THEME),
            lambda: extras.apply_appearance(str(game), cfg, skin="Moon"),
            lambda: extras.install(str(game), cfg, "textures"),
            lambda: music_library.set_music_enabled(game, True),
        ):
            with pytest.raises(OSError):
                pool.submit(run).result(timeout=10)
    assert (game / "config/system.conf").read_bytes() == ORIGINAL


def test_installed_photo_and_legacy_pack_compatibility(game: Path, tmp_path: Path) -> None:
    photo = QImage(800, 100, QImage.Format.Format_RGB32)
    photo.fill(QColor("red"))
    for x in range(100):
        for y in range(100):
            photo.setPixelColor(x, y, QColor("blue"))
    conf = game / "config/system.conf"
    conf.write_bytes(b"\xef\xbb\xbfscale_background = 0\r\n" + ORIGINAL[3:])
    assert not scale_background(game)
    cfg = str(tmp_path / "user/config.json")
    extras.apply_appearance(str(game), cfg, skin="Moon")
    name = install_theme(game, ID, replace(THEME, wallpaper="image", image="0" * 64), image=photo)
    assert (
        QImage(str(game / "skin" / name / "textures/bg.png")).pixelColor(8, 800).name() == "#ff0000"
    )
    switch_back(game, ID)
    assert extras.current_skin(str(game)) == "Monokai Moon"
    extras.apply_appearance(str(game), cfg, restore=True)
    assert extras.current_skin(str(game)) == "Grey"


@pytest.mark.parametrize("original", [b"\xef\xbb\xbf# last line", b"# first\n\nlast = 1", b""])
def test_absent_settings_round_trip(game: Path, original: bytes) -> None:
    conf = game / "config/system.conf"
    conf.write_bytes(original)
    install_theme(game, ID, replace(THEME, font="inter"))
    remove_theme(game, ID)
    assert conf.read_bytes() == original


def test_switching_between_generated_predecessors_and_removing_in_any_order(game: Path) -> None:
    assert install_state(game, ID).folder is None
    a = install_theme(game, ID, THEME)
    b = install_theme(game, OTHER, THEME)
    install_theme(game, ID, THEME)
    status = install_state(game, ID)
    assert status.active and status.folder == a and status.can_undo and not status.recovery_pending
    remove_theme(game, ID)
    assert extras.current_skin(str(game)) == b
    remove_theme(game, OTHER)
    assert (game / "config/system.conf").read_bytes() == ORIGINAL


def test_switch_back_then_undo_inactive_font_edit_preserves_active_preferences(game: Path) -> None:
    conf = game / "config/system.conf"
    folder = game / "skin" / install_theme(game, ID, replace(THEME, font="inter"))
    install_theme(game, ID, THEME)
    switch_back(game, ID)
    undo_theme(game, ID)
    assert (folder / "fonts/InterEDO-Regular.ttf").is_file()
    assert conf.read_bytes() == ORIGINAL


def test_shadowed_settings_trailing_spaces_and_external_empty_dir(game: Path) -> None:
    conf = game / "config/system.conf"
    raw = b"\xef\xbb\xbfskin=old\r\n  skin \t= Grey  \t\n# end"
    conf.write_bytes(raw)
    folder = game / "skin" / install_theme(game, ID, THEME)
    user_dir = folder / "textures"
    user_dir.mkdir()
    install_theme(game, ID, replace(THEME, wallpaper="gradient"))
    remove_theme(game, ID)
    assert user_dir.is_dir() and not list(user_dir.iterdir())
    assert conf.read_bytes() == raw

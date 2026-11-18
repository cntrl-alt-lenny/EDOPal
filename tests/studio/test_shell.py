from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSize, Qt
from pytestqt.qtbot import QtBot

import config
from studio.context import Installation
from studio.geometry import fit_geometry
from studio.shell import StudioWindow


def test_sidebar_navigation_and_keyboard(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    assert window.windowTitle() == "EDOPro Studio"
    assert list(window.pages) == [
        "cards",
        "skins",
        "sleeves",
        "sounds",
        "music",
        "decks",
        "backup",
        "settings",
    ]
    for key, button in window.nav.items():
        button.click()
        assert window.current is window.pages[key]
        assert button.isChecked()
        assert sum(item.isChecked() for item in window.nav.values()) == 1
    window.nav["cards"].setFocus()
    qtbot.keyClick(window.nav["cards"], Qt.Key.Key_Down)
    assert window.nav["skins"].hasFocus()
    qtbot.keyClick(window.nav["skins"], Qt.Key.Key_Return)
    assert window.current.id == "skins"


def test_picker_switch_validates_persists_and_broadcasts(
    make_window: Callable[..., StudioWindow],
    qtbot: QtBot,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = make_window()
    second = tmp_path / "Portable"
    second.mkdir()
    (second / "cards.cdb").touch()
    monkeypatch.setattr(config, "find_edopro_folders", lambda guesses: [str(second)])
    window.change.click()
    qtbot.waitUntil(lambda: window.picker.list.count() == 1)
    window.picker.use.click()
    qtbot.waitUntil(lambda: window.ctx.installation.path == str(second))
    assert window.folder_name.text() == "Portable"
    assert window.ctx.cfg.edopro_path == str(second)
    assert config._load_config_file(window.ctx.cfg.config_path)["edopro_path"] == str(second)
    assert window.pages["cards"].options.scope.group.checkedId() == 0


def test_missing_install_and_uninitialized_game_gate_sync(
    make_window: Callable[..., StudioWindow],
) -> None:
    window = make_window()
    card = window.current
    window.ctx.set_installation(Installation())
    assert not card.options.start.isEnabled()
    assert not card.settings.coverage.isEnabled()
    assert "folder" in card.options.status.text()
    assert window.change.isEnabled()
    window.ctx.set_installation(Installation("EmptyGame", False))
    assert "databases" in card.options.status.text()
    assert not card.options.start.isEnabled()


def test_clamp_across_monitors_and_small_screens() -> None:
    available = QRect(-1280, 120, 1280, 760)
    clamped = fit_geometry(QRect(5000, 5000, 4000, 2000), available, QSize(8, 30))
    assert available.contains(clamped)
    assert clamped.size() == QSize(1272, 730)
    small = QRect(700, 50, 800, 500)
    assert fit_geometry(None, small) == small
    default = fit_geometry(None, QRect(0, 0, 1920, 1080))
    assert default == QRect(370, 160, 1180, 760)


def test_geometry_is_restored_from_config_local_settings(
    make_window: Callable[..., StudioWindow],
) -> None:
    window = make_window()
    available = window.screen().availableGeometry()
    window.resize(min(1010, available.width() - 12), min(660, available.height() - 32))
    window.move(0, 6)
    size, pos = window.size(), window.pos()
    window.close()
    assert Path(window.settings.fileName()).parent == Path(window.ctx.cfg.config_path).parent
    restored = make_window()
    assert restored.size() == size
    assert restored.pos() == pos


def test_empty_installation_cta_and_browse_validation(
    make_window: Callable[..., StudioWindow],
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = make_window()
    game_path = window.ctx.installation.path
    monkeypatch.setattr(config, "find_edopro_folders", lambda _: [])
    window.ctx.set_installation(Installation())
    window.current.options.connect_game.click()
    assert window.content.currentWidget() is window.picker
    qtbot.waitUntil(lambda: "No installations" in window.picker.hint.text())
    monkeypatch.setattr(window.ctx, "choose_folder", lambda _: str(tmp_path))
    window.picker.browse.click()
    qtbot.waitUntil(lambda: "containing EDOPro" in window.picker.hint.text())
    assert not window.ctx.installation.ready
    monkeypatch.setattr(window.ctx, "choose_folder", lambda _: game_path)
    window.picker.browse.click()
    qtbot.waitUntil(lambda: window.ctx.installation.ready)
    assert window.content.currentWidget() is window.stack

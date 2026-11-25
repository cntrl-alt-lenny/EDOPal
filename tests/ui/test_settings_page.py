from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton

from edopal.dev.preview import make_core
from edopal.ui import theme
from edopal.ui.settings_page import Mascot, SettingsPage
from edopal.ui.shell import ShellWindow


def test_settings_shows_on_launch_and_current_game(qapp, qtbot, tmp_path, monkeypatch):
    theme.apply(qapp)
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    window = ShellWindow(core, [], "settings")
    qtbot.addWidget(window)
    window.show()
    page = window.pages["settings"]
    assert isinstance(page, SettingsPage)
    assert "On Launch" in [label.text() for label in page.findChildren(QLabel)]
    folders = {button.accessibleName(): button for button in page.findChildren(QPushButton)}
    qtbot.mouseClick(folders["Game Installation"], Qt.MouseButton.LeftButton)
    assert window.picker is not None
    window.picker.reject()
    chosen = tmp_path / "game"
    chosen.mkdir()
    core.installs.select(chosen)
    assert page._game_path.accessibleName() == str(chosen)

    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url) or True)
    qtbot.mouseClick(folders["Game Installation"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(folders["Release Notes"], Qt.MouseButton.LeftButton)
    assert urls[0] == QUrl.fromLocalFile(str(chosen))
    assert urls[1].toString().endswith("/releases")


def test_mascot_glow_tracks_the_pointer(qapp, qtbot, tmp_path):
    theme.apply(qapp)
    core = make_core(Path("/game"), tmp_path / "packs", tmp_path / "home")
    window = ShellWindow(core, [], "settings")
    qtbot.addWidget(window)
    window.show()
    mascot = window.findChild(Mascot)
    qtbot.mouseMove(window.pages["settings"], QPoint(20, 20))
    neutral = mascot.grab().toImage()
    qtbot.mouseMove(mascot, QPoint(43, 65))
    first = mascot.grab().toImage()
    qtbot.mouseMove(mascot, QPoint(70, 43))
    second = mascot.grab().toImage()
    assert first != neutral and second != first
    assert mascot.pointer.toPoint() == QPoint(70, 43)
    qtbot.mouseMove(window.pages["settings"], QPoint(20, 20))
    assert mascot.pointer is None and mascot.grab().toImage() == neutral

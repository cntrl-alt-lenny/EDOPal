from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

import gamesettings as gs
from studio.context import Installation
from studio.pages.settings import SettingsPage
from studio.pages.settings_display import DisplayView
from studio.shell import PAGES, StudioWindow
from tools.studio_display import DemoSettings, seed


@pytest.fixture
def display(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> tuple[StudioWindow, DisplayView, Path]:
    monkeypatch.setattr(
        "studio.shell.PAGES", tuple(DemoSettings if p is SettingsPage else p for p in PAGES)
    )
    window = make_window()
    conf = seed(Path(window.ctx.installation.path))
    window.navigate("settings")
    page = window.current
    page.tiles["display"].click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    view = page.stack.currentWidget()
    assert view.mode == "edit"
    return window, view, conf


def click_named(view: DisplayView, name: str) -> None:
    next(b for b in view.findChildren(QPushButton) if b.text() == name and b.isVisible()).click()


def test_review_edit_apply_undo_restore(display: tuple, qtbot: QtBot) -> None:
    window, view, conf = display
    original = conf.read_bytes()
    view.profile.setFocus()
    qtbot.keyClick(view.profile, Qt.Key.Key_Return)
    assert view.profile.view().isVisible()
    qtbot.keyClick(view.profile.view(), Qt.Key.Key_Down)
    qtbot.keyClick(view.profile.view(), Qt.Key.Key_Return)
    assert view.profile.currentIndex() == 1
    view.controls["size"].setValue(14)
    view.primary.click()
    assert {c.key for c in view.pending} == {"dpi_scale", "fullscreen", "textfont"}
    assert conf.read_bytes() == original
    qtbot.keyClick(view.primary, Qt.Key.Key_Escape)
    assert view.mode == "edit"
    assert view.controls["size"].value() == 14
    view.primary.click()
    qtbot.keyClick(view.primary, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "done"
    assert view.primary.text() == "Applied" and not view.primary.isEnabled()
    assert b"fonts/Inter-Regular.ttf 14" in conf.read_bytes()
    changed = conf.read_bytes()
    click_named(view, "Restore original")
    assert view.mode == "review" and view.primary.text() == "Restore original"
    view.primary.click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert conf.read_bytes() == original
    click_named(view, "Undo last change")
    view.primary.click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert conf.read_bytes() == changed
    view.back.click()
    view.back.click()
    assert window.current.section == "home"


def test_device_profile_save_and_reopen(display: tuple, qtbot: QtBot) -> None:
    window, view, conf = display
    original = conf.read_bytes()
    view.profile.setCurrentIndex(4)
    click_named(view, "Motion and graphics")
    view.controls["maxFPS"].setValue(40)
    view.secondary.click()
    assert not view.primary.isEnabled()
    qtbot.keyClicks(view.name, "Evening play")
    qtbot.keyClick(view.name, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.profile.currentText() == "Evening play"
    assert conf.read_bytes() == original
    view.back.click()
    window.current.tiles["display"].click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.profile.currentText() == "Current settings"
    view.profile.setCurrentText("Evening play")
    view.primary.click()
    assert next(c.after for c in view.pending if c.key == "maxFPS") == "40"


def test_busy_and_retry_preserve_choice(
    display: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, view, conf = display
    original = conf.read_bytes()
    entered, release = Event(), Event()

    def running(_: Path) -> bool:
        entered.set()
        return release.wait(10)

    monkeypatch.setattr("music.settings._game_running", running)
    view.profile.setCurrentIndex(3)
    view.primary.click()
    view.primary.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.ctx.busy and not view.hidden()
        assert not window.navigate("cards")
        assert "selected" in view.note.text()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "blocked" and view.block.retry.isVisible()
    assert not view.primary.isVisible()
    assert conf.read_bytes() == original
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    view.block.retry.click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "done" and view.snapshot.values["dpi_scale"] == "2.000000"


def test_folder_empty_unready_error_and_reselection(display: tuple, qtbot: QtBot) -> None:
    window, view, conf = display
    selected = window.ctx.installation
    window.ctx.set_installation(Installation())
    assert view.mode == "empty"
    window.ctx.installations_requested.disconnect(window.open_picker)
    with qtbot.waitSignal(window.ctx.installations_requested):
        click_named(view, "Choose folder")
    window.ctx.set_installation(Installation(selected.path))
    assert view.mode == "unready"
    click_named(view, "Retry")
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "edit" and window.ctx.installation.ready
    conf.unlink()
    window.ctx.set_installation(selected)
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "error"
    seed(Path(selected.path))
    click_named(view, "Retry")
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "edit"


def test_stale_read_is_ignored(
    display: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, view, _ = display
    selected = window.ctx.installation
    original = gs.DisplaySettings.inspect
    release = Event()

    def delayed(self: gs.DisplaySettings) -> gs.Snapshot:
        release.wait(10)
        return original(self)

    monkeypatch.setattr(gs.DisplaySettings, "inspect", delayed)
    try:
        window.ctx.set_installation(selected)
        window.ctx.set_installation(Installation())
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "empty" and view.snapshot is None


def test_stale_target_requires_a_new_review(display: tuple, qtbot: QtBot) -> None:
    window, view, conf = display
    view.profile.setCurrentIndex(3)
    view.primary.click()
    edited = conf.read_bytes().replace(b"1.000000", b"1.1")
    conf.write_bytes(edited)
    view.primary.click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "failed" and conf.read_bytes() == edited
    view.primary.click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert view.mode == "edit" and not view.primary.isEnabled()

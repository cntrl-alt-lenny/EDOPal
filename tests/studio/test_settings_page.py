import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

import config
import main
from studio.pages import settings, settings_shortcuts, settings_views
from studio.pages.settings_shortcuts import Shortcut
from studio.shell import StudioWindow
from studio.widgets import Button


def control(window: StudioWindow, caption: str, qtbot: QtBot) -> Button:
    def matches() -> list[Button]:
        return [
            b for b in window.current.findChildren(Button) if b.isVisible() and b.text() == caption
        ]

    qtbot.waitUntil(lambda: bool(matches()))
    return matches()[0]


def test_about_shows_brand_credits_and_independence(make_window: Callable, qtbot: QtBot) -> None:
    window = make_window()
    window.navigate("settings")
    page = window.current
    page.tiles["about"].click()
    qtbot.waitUntil(
        lambda: any(
            not w.pixmap().isNull() and w.isVisible() for w in page.detail.findChildren(QLabel)
        )
    )
    lines = [w.text() for w in page.detail.findChildren(QLabel) if w.isVisible()]
    assert "Your unofficial companion for EDOPro." in lines
    assert (
        "EDOPal is an independent fan-made companion for EDOPro. EDOPro is a Project Ignis project. "
        "EDOPal is unaffiliated with Project Ignis, Konami, and Shueisha and has no endorsement from them. "
        "Yu-Gi-Oh! and related marks belong to their respective rights holders."
    ) in lines
    assert not page.hero.isVisible()
    marks = [
        w.pixmap().toImage() for w in page.detail.findChildren(QLabel) if not w.pixmap().isNull()
    ]
    assert window.windowIcon().pixmap(38, 38).toImage() in marks


def test_both_editors_save_all_choices_across_relaunch(make_window: Callable, qtbot: QtBot) -> None:
    window = make_window()
    window.nav["settings"].click()
    page = window.current
    page.tiles["defaults"].click()
    page.defaults.scope.buttons[1].click()
    for toggle in page.defaults.toggles.values():
        toggle.click()
    page.defaults.done.click()
    qtbot.waitUntil(lambda: page.section == "home")
    cfg = config.Config(window.ctx.cfg.argv)
    assert cfg.studio_scope == "library"
    assert cfg.studio_repair and cfg.studio_theme and cfg.force and cfg.save_report
    assert not cfg.field_art and cfg.original_text
    window.nav["cards"].click()
    cards = window.current
    assert cards.options.scope.group.checkedId() == 0
    cards.options.settings.click()
    assert all(
        cards.settings.toggles[k].isChecked() for k in ("theme", "force", "repair", "save_report")
    )
    cards.settings.toggles["force"].click()
    cards.settings.done.click()
    qtbot.waitUntil(lambda: cards.state == "options")
    assert not config.Config(cfg.argv).force
    window.nav["settings"].click()
    page.tiles["defaults"].click()
    assert not page.defaults.toggles["force"].isChecked()
    page.defaults.toggles["force"].click()
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert not config.Config(cfg.argv).force


def test_save_failure_keeps_choices_for_retry(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.navigate("settings")
    editor = window.current
    editor.tiles["defaults"].click()
    old = window.ctx.cfg.force
    editor.defaults.toggles["force"].click()
    monkeypatch.setattr(config, "save_settings", lambda *_: False)
    editor.defaults.done.click()
    qtbot.waitUntil(lambda: not window.ctx.busy)
    assert editor.section == "defaults" and editor.defaults.done.isEnabled()
    assert window.ctx.cfg.force == old
    assert "Could not save" in window.notice.message.text()


def test_game_folders_switch_forget_and_readd(
    make_window: Callable, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    first = window.ctx.installation.path
    second = tmp_path / "Portable"
    second.mkdir()
    (second / "EDOPro.exe").touch()
    monkeypatch.setattr(config, "find_edopro_folders", lambda _: [str(second)])
    window.navigate("settings")
    page = window.current
    page.tiles["games"].click()
    page.find_games.click()
    qtbot.waitUntil(lambda: window.picker.list.count() == 1)
    window.picker.use.click()
    qtbot.waitUntil(lambda: window.ctx.installation.path == str(second))
    assert not window.ctx.installation.ready
    assert window.ctx.known_installations() == [first, str(second)]
    control(window, "Use this game", qtbot).click()
    qtbot.waitUntil(lambda: window.ctx.installation.path == first)
    monkeypatch.setattr(window.ctx, "confirm", lambda *_: True)
    control(window, "Forget", qtbot).click()
    qtbot.waitUntil(lambda: not window.ctx.installation.path)
    assert window.ctx.known_installations() == [str(second)]
    assert Path(first, "cards.cdb").is_file()
    assert json.loads(Path(window.ctx.cfg.config_path).read_text())["edopro_path"] is None
    control(window, "Use this game", qtbot).click()
    qtbot.waitUntil(lambda: window.ctx.installation.path == str(second))


def test_storage_remove_clear_busy_and_failure(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.resize(960, 600)
    window.navigate("settings")
    page = window.current
    for name in ("blue-grid", "gold"):
        directory = page.cache.root / f"pack-{name}"
        directory.mkdir(parents=True)
        (directory / "content.zip").write_bytes(bytes(2048))
    page.tiles["storage"].click()
    qtbot.waitUntil(lambda: hasattr(page, "remove_buttons"))
    game_files = set(Path(window.ctx.installation.path).rglob("*"))
    assert any(w.text() == "2.0 KB" for w in page.findChildren(QLabel))
    monkeypatch.setattr(window.ctx, "confirm", lambda *_: False)
    page.remove_buttons["blue-grid"].click()
    assert (page.cache.root / "pack-blue-grid/content.zip").exists()
    monkeypatch.setattr(window.ctx, "confirm", lambda *_: True)
    entered, release = threading.Event(), threading.Event()
    original = page.cache.remove_pack

    def remove(key: str) -> None:
        entered.set()
        assert release.wait(3)
        original(key)

    monkeypatch.setattr(page.cache, "remove_pack", remove)
    page.remove_buttons["blue-grid"].click()
    qtbot.waitUntil(entered.is_set)
    assert not window.change.isEnabled() and not window.navigate("cards")
    release.set()
    qtbot.waitUntil(lambda: set(page.remove_buttons) == {"gold"})
    with monkeypatch.context() as mp:
        mp.setattr(page.cache, "clear", Mock(side_effect=OSError("Pack is in use")))
        page.clear_packs.click()
        qtbot.waitUntil(lambda: "in use" in page.message.text())
        view = page.detail.viewport()
        qtbot.waitUntil(lambda: view.rect().contains(page.message.mapTo(view, QPoint())))
        assert not window.ctx.busy
    page.clear_packs.click()
    qtbot.waitUntil(lambda: not page.remove_buttons)
    assert not page.clear_packs.isEnabled()
    assert set(Path(window.ctx.installation.path).rglob("*")) == game_files


def test_leave_storage_check_and_return_before_its_first_result(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.navigate("settings")
    page = window.current
    entered, release = threading.Event(), threading.Event()
    inspect = settings.inspect_storage

    def read(*args: object) -> settings.Storage:
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            raise OSError("Old read")
        return inspect(*args)

    monkeypatch.setattr(settings, "inspect_storage", read)
    page.tiles["storage"].click()
    qtbot.waitUntil(entered.is_set)
    try:
        assert window.navigate("cards")
        assert window.navigate("settings")
        qtbot.waitUntil(lambda: page.message.text() == "")
    finally:
        release.set()
    qtbot.waitUntil(lambda: not page.pending)
    assert page.section == "storage" and page.message.text() == ""
    assert page.clear_packs.isVisible()


def test_update_survives_offline_check_and_restart(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.navigate("settings")
    page = window.current
    page.tiles["updates"].click()
    monkeypatch.setattr(main, "check_for_update", AsyncMock(return_value="v9.0.0"))
    page.check.click()
    qtbot.waitUntil(lambda: window.release.isVisible())
    assert page.get_release.isVisible()
    open_link = Mock(return_value=True)
    monkeypatch.setattr(settings_views.QDesktopServices, "openUrl", open_link)
    page.get_release.click()
    open_link.assert_called_once_with(QUrl(settings.PROJECT + "/releases/latest"))
    monkeypatch.setattr(main, "check_for_update", AsyncMock(side_effect=OSError("offline")))
    page.check.click()
    qtbot.waitUntil(lambda: "connection" in page.news.text())
    assert page.get_release.isVisible() and page.check.isEnabled()
    second = make_window()
    assert second.release.isVisible() and second.ctx.latest == "v9.0.0"
    monkeypatch.setattr(main, "check_for_update", AsyncMock(return_value=None))
    page.check.click()
    qtbot.waitUntil(lambda: not window.release.isVisible())
    assert "latest" in page.news.text()


@pytest.mark.parametrize(
    "windows",
    (
        False,
        pytest.param(
            True, marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows shell links")
        ),
    ),
)
def test_create_remove_shortcut_controls(
    make_window: Callable,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows: bool,
) -> None:
    link = Shortcut("Desktop", tmp_path / ("test.lnk" if windows else "test.desktop"), windows)
    monkeypatch.setattr(settings, "shortcut_locations", lambda: (link,))
    window = make_window()
    window.navigate("settings")
    page = window.current
    page.tiles["shortcuts"].click()
    control(window, "Create shortcut", qtbot).click()
    qtbot.waitUntil(lambda: link.path.is_file() and not window.ctx.busy and not page.message.text())
    assert settings_shortcuts.shortcut_status(link, Path(sys.executable), "") == "installed"
    control(window, "Remove shortcut", qtbot).click()
    qtbot.waitUntil(
        lambda: not link.path.exists() and not window.ctx.busy and not page.message.text()
    )
    link.path.write_bytes(b"unrelated file")
    page.open_section("shortcuts")
    qtbot.waitUntil(lambda: not page.message.text())
    assert any("Another file" in w.text() for w in page.findChildren(QLabel))
    assert link.path.read_bytes() == b"unrelated file"


def test_source_shortcuts_and_classic_handoff(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.navigate("settings")
    page = window.current
    page.tiles["shortcuts"].click()
    assert any("source" in w.text() or "Finder" in w.text() for w in page.findChildren(QLabel))
    page.back.click()
    launch = Mock()
    monkeypatch.setattr(settings.subprocess, "Popen", launch)
    page.classic.click()
    qtbot.waitUntil(lambda: not window.isVisible())
    args = launch.call_args.args[0]
    assert args[0] == sys.executable
    assert args[-5:] == [
        "--legacy-gui",
        "--config",
        window.ctx.cfg.config_path,
        "--edopro-path",
        window.ctx.installation.path,
    ]

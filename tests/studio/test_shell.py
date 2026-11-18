from collections.abc import Callable
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSize, Qt, QThread
from PySide6.QtWidgets import QLabel, QWidget
from pytestqt.qtbot import QtBot

import config
from studio.context import Installation
from studio.geometry import fit_geometry
from studio.shell import StudioWindow
from studio.tasks import WorkerContext


def test_sidebar_navigation_and_keyboard(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.resize(960, 600)
    assert window.windowTitle() == "EDOPal"
    assert window.nav["backup"].text() == "Backup && transfer"
    assert (
        window.nav["backup"].accessibleName()
        == window.nav["backup"].toolTip()
        == "Backup & transfer"
    )
    assert [line.text() for line in window.rail.findChildren(QLabel) if line.text()][:2] == [
        "EDOPal",
        "Unofficial EDOPro companion",
    ]
    from studio.shell import PAGES

    assert list(window.pages) == [page.id for page in PAGES]
    existing = [
        "cards",
        "skins",
        "sleeves",
        "sounds",
        "music",
        "decks",
        "mydecks",
        "replays",
        "backup",
        "health",
        "settings",
    ]
    assert [key for key in window.pages if key in existing] == existing
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
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)


@pytest.mark.parametrize("destroy_window", (True, False))
def test_destroyed_owner_drops_queued_and_late_events(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, destroy_window: bool
) -> None:
    window = make_window()
    runner = window.ctx.tasks
    owner = window if destroy_window else QWidget(window)
    posted = Event()
    received = []

    def work(worker: WorkerContext) -> None:
        worker.progress(1)
        posted.set()
        assert worker.cancel.wait(5)
        worker.progress(2)
        raise ValueError("late error")

    job = runner.start(work, owner=None if destroy_window else owner)
    disposed_on = []
    job.destroyed.connect(lambda: disposed_on.append(QThread.currentThread()))
    for signal in (job.progress, job.result, job.error, job.cancelled, job.finished):
        signal.connect(lambda *args: received.append(args))
    assert posted.wait(3)
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
    qtbot.waitUntil(lambda: not runner.jobs)
    qtbot.waitUntil(lambda: bool(disposed_on))
    assert disposed_on == [QCoreApplication.instance().thread()]
    assert job.cancel_event.is_set()
    assert not received


def test_each_selected_row_fits_the_scrolling_viewport(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.resize(960, 600)
    window.nav["cards"].setFocus()
    # The update link also takes space out of the scrolling group.
    window._update("v9.0")
    viewport = window.rail_scroll.viewport()
    for key, button in window.nav.items():
        qtbot.keyClick(button, Qt.Key.Key_Return)
        assert window.current.id == key
        if key != "settings":
            row = button.rect().translated(button.mapTo(viewport, button.rect().topLeft()))
            assert viewport.rect().contains(row)
        qtbot.keyClick(button, Qt.Key.Key_Down)


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


def test_browse_bundle_saves_only_its_validated_data_root(
    make_window: Callable[..., StudioWindow],
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = make_window()
    settings = Path(window.ctx.cfg.config_path)
    before = settings.read_bytes() if settings.exists() else None
    original = window.ctx.installation.path
    bundle = tmp_path / "Applications/EDOPro.app"
    data = bundle / "Contents/Resources/ProjectIgnis"
    data.mkdir(parents=True)
    (bundle / "Contents/Info.plist").touch()
    from types import SimpleNamespace

    # Simulate Finder discovery without changing the host file-lock implementation.
    monkeypatch.setattr(config, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(config, "find_edopro_folders", lambda _: [original])
    monkeypatch.setattr(window.ctx, "choose_folder", lambda _: str(bundle))
    window.change.click()
    qtbot.waitUntil(lambda: window.picker.list.count() == 1)
    window.picker.browse.click()
    qtbot.waitUntil(lambda: "containing EDOPro" in window.picker.hint.text())
    assert window.ctx.installation.path == original
    assert (settings.read_bytes() if settings.exists() else None) == before

    (data / "cards.cdb").touch()
    window.picker.browse.click()
    qtbot.waitUntil(lambda: window.ctx.installation.path == str(data))
    assert window.ctx.installation.ready
    assert config._load_config_file(str(settings))["edopro_path"] == str(data)


def test_many_picker_results_leave_room_for_the_action_row(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.resize(960, 600)
    folders = [str(Path(window.ctx.installation.path).parent / f"EDOPro {i}") for i in range(8)]
    monkeypatch.setattr(config, "find_edopro_folders", lambda _: folders)
    window.change.click()
    picker = window.picker
    qtbot.waitUntil(lambda: picker.list.count() == len(folders))
    assert picker.rect().contains(picker.use.geometry())
    assert window.size() == QSize(960, 600)
    picker.list.setFocus()
    qtbot.keyClick(picker.list, Qt.Key.Key_End)
    assert picker.list.currentRow() == len(folders) - 1
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert window.content.currentWidget() is window.stack


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
    qtbot.waitUntil(lambda: "No EDOPro folders" in window.picker.hint.text())
    monkeypatch.setattr(window.ctx, "choose_folder", lambda _: str(tmp_path))
    window.picker.browse.click()
    qtbot.waitUntil(lambda: "containing EDOPro" in window.picker.hint.text())
    assert not window.ctx.installation.ready
    monkeypatch.setattr(window.ctx, "choose_folder", lambda _: game_path)
    window.picker.browse.click()
    qtbot.waitUntil(lambda: window.ctx.installation.ready)
    assert window.content.currentWidget() is window.stack


def test_lazy_pages_receive_latest_folder_and_close_only_built_pages(
    make_window: Callable, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    assert list(window.pages.built) == ["cards"]
    assert list(window.nav) == list(window.pages) and "settings" in window.pages
    assert len(window.pages.built) == 1
    new = tmp_path / "Second"
    new.mkdir()
    window.ctx.set_installation(Installation(str(new)))
    settings = window.pages["settings"]
    assert settings.tiles["games"].hint.text() == "Second"
    window.ctx.set_installation(Installation())
    assert settings.tiles["games"].hint.text() == "No EDOPro folder selected"
    assert window.current.id == "cards" and len(window.pages.built) == 2
    closed = []
    for key, page in window.pages.built.items():
        monkeypatch.setattr(page, "request_close", lambda key=key: closed.append(key) or True)
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert window.close()
    assert closed == ["cards", "settings"] and len(window.pages.built) == 2


def test_escape_on_every_page_root_with_and_without_background_work(
    make_window: Callable, qtbot: QtBot
) -> None:
    window = make_window()
    for key in window.pages:
        assert window.navigate(key)
        qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
        qtbot.keyClick(window, Qt.Key.Key_Escape)
        assert window.isVisible() and window.current.id == key
        release = Event()
        job = window.ctx.tasks.start(lambda _, release=release: release.wait(5))
        try:
            for writing in (False, True):
                window.ctx.set_busy(writing)
                qtbot.keyClick(window.nav[key], Qt.Key.Key_Escape)
                assert window.isVisible() and not window.closing and not job.cancel_event.is_set()
        finally:
            window.ctx.set_busy(False)
            release.set()
        qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)


def test_escape_during_unlocked_music_job_and_search(make_window: Callable, qtbot: QtBot) -> None:
    window = make_window()
    window.navigate("music")
    page = window.current
    qtbot.waitUntil(lambda: page.state == "home")
    release = Event()
    page.run("Reading tracks", lambda _: release.wait(5), lambda _: page.home())
    try:
        qtbot.keyClick(page.progress.cancel, Qt.Key.Key_Escape)
        assert not window.ctx.busy and not page.job.cancel_event.is_set()
    finally:
        release.set()
    qtbot.waitUntil(lambda: page.state == "home")
    window.navigate("decks")
    search = window.current.browser.search
    search.setText("search term")
    qtbot.keyClick(search, Qt.Key.Key_Escape)
    assert not search.text() and window.isVisible()


@pytest.mark.parametrize("width,height", [(533, 533), (531, 531), (1280, 800)])
def test_scaled_work_area_keeps_navigation_picker_and_footer_reachable(
    make_window: Callable, qtbot: QtBot, width: int, height: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    window = make_window()
    window.setMinimumSize(1, 1)
    window.resize(width, height)
    window._update("v5.6.0")
    for key, button in window.nav.items():
        button.setFocus()
        qtbot.keyClick(button, Qt.Key.Key_Return)
        assert window.current.id == key and window.rect().contains(
            button.mapTo(window, button.rect().center())
        )
        assert button.accessibleName() == button.toolTip()
    window.navigate("cards")
    small = width < 960
    assert (window.nav["cards"].text() == "") == small
    assert window.minimumSizeHint().boundedTo(window.size()) == window.minimumSizeHint()
    for target in (window.current.options.start, window.change):
        target.setFocus()
        QApplication.processEvents()
        assert window.rect().contains(target.mapTo(window, target.rect().bottomRight()))
    monkeypatch.setattr(config, "find_edopro_folders", lambda _: [window.ctx.installation.path])
    window.change.click()
    qtbot.waitUntil(lambda: window.picker.use.isEnabled())
    window.picker.use.setFocus()
    assert window.rect().contains(window.picker.use.mapTo(window, QPoint(2, 2)))
    qtbot.keyClick(window.picker.use, Qt.Key.Key_Escape)
    assert window.content.currentWidget() is window.stack and window.change.hasFocus()


@pytest.mark.parametrize("maximized", [True, False])
def test_wayland_restores_size_and_maximization_without_placing_window(
    make_window: Callable, monkeypatch: pytest.MonkeyPatch, maximized: bool
) -> None:
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication

    window = make_window()
    window.settings.setValue("window/normal", QRect(-8000, 7000, 1040, 680))
    window.settings.setValue("window/maximized", maximized)
    monkeypatch.setattr(QApplication, "platformName", lambda: "wayland-egl")
    move = Mock(side_effect=AssertionError("Compositor owns placement"))
    monkeypatch.setattr(window, "move", move)
    resize, maximize = Mock(), Mock()
    monkeypatch.setattr(window, "resize", resize)
    monkeypatch.setattr(window, "showMaximized", maximize)
    from studio.widgets.window import restore_window, save_window

    restore_window(window, window.settings)
    frame = window.windowHandle().frameMargins()
    decor = QSize(frame.left() + frame.right(), frame.top() + frame.bottom())
    resize.assert_called_once_with(
        QSize(1040, 680).boundedTo(window.screen().availableSize() - decor)
    )
    save_window(window, window.settings)
    assert window.settings.value("window/size") == window.size()
    assert (
        window.settings.value("window/normal").topLeft() == QRect(-8000, 7000, 1040, 680).topLeft()
    )
    assert maximize.called == maximized
    move.assert_not_called()


def test_confirmation_has_named_commit_and_safe_escape(make_window: Callable) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox

    window = make_window()
    for title, safe, commit, affirmative in (
        ("Discard unsaved changes?", "Cancel", "Discard changes", None),
        ("Remove downloaded files?", "Cancel", "Remove downloaded files", None),
        ("Review restore", "Cancel", "Restore to ProjectIgnis", "Restore to ProjectIgnis"),
    ):
        observed = []

        def dismiss(observed: list = observed) -> None:
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox)
            observed.append((box.defaultButton().text(), {b.text() for b in box.buttons()}))
            QTest.keyClick(box, Qt.Key.Key_Escape)

        QTimer.singleShot(0, dismiss)
        assert not window.ctx.confirm(title, "Review this change.", affirmative)
        assert observed == [(safe, {safe, commit})]


def test_short_sidebar_fits_the_upcoming_sections_and_release(
    make_window: Callable, qtbot: QtBot
) -> None:
    from tools.studio_shell_shots import preview_deck_sections

    with preview_deck_sections():
        window = make_window()
    window.resize(1280, 800)
    window._update("v5.6.0")
    viewport = window.rail_scroll.viewport()
    qtbot.waitUntil(lambda: window.rail_scroll.verticalScrollBar().maximum() == 0)
    for key, button in window.nav.items():
        target = window if key == "settings" else viewport
        assert target.rect().contains(
            button.rect().translated(button.mapTo(target, button.rect().topLeft()))
        )
    assert window.release.isVisible()
    # There is room even for a thirteenth ordinary section in addition to Settings.
    assert viewport.height() - window.rail.entries.minimumSize().height() >= (
        window.nav["formats"].height() + window.rail.entries.spacing()
    )
    assert list(window.pages.built) == ["cards"]
    assert window.navigate("practice") and window.navigate("formats")


def test_worker_callable_is_disposed_on_gui_thread(make_window: Callable, qtbot: QtBot) -> None:
    import threading
    import weakref

    runner = make_window().ctx.tasks
    released = []
    gate = Event()

    class Work:
        def __call__(self, _: WorkerContext) -> None:
            assert gate.wait(3)

    work = Work()
    watcher = weakref.finalize(work, lambda: released.append(threading.get_ident()))
    runner.start(work)
    del work
    gate.set()
    qtbot.waitUntil(lambda: not runner.jobs)
    assert not watcher.alive and released == [threading.get_ident()]


def test_unavailable_volume_updates_folder_bar(
    make_window: Callable, qtbot: QtBot, tmp_path: Path
) -> None:
    window = make_window()
    missing = tmp_path / "unplugged"
    window.ctx.set_installation(Installation(str(missing), ready=True))
    qtbot.waitUntil(lambda: window.folder_name.text() == "Folder unavailable")
    assert window.folder_name.toolTip() == str(missing)

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from edopal import app
from edopal.core.feature import FeatureSpec
from edopal.core.install import RunState
from edopal.dev.preview import make_core
from edopal.ui import theme


@pytest.fixture
def shell(qtbot, tmp_path, monkeypatch, qapp):
    theme.apply(qapp)
    game = tmp_path / "game"
    game.mkdir()
    core = make_core(game, tmp_path / "packs", tmp_path / "home")
    trace = []
    badge = {"value": None}

    def spec(key):
        def page(_):
            trace.append((key, "created"))
            return QLabel(key)

        return FeatureSpec(
            key,
            key.title(),
            key,
            0,
            page,
            on_install_changed=lambda _: trace.append((key, "selected")),
            on_game_exited=lambda _: trace.append((key, "exited")),
            badge=lambda _: badge["value"],
        )

    monkeypatch.setattr(app, "load_features", lambda _: [spec("themes"), spec("music")])
    window = app.build_window(core, start="music")
    qtbot.addWidget(window)
    window.show()
    yield core, window, trace, badge
    window.close()


def test_navigation_badges_and_keyboard(qtbot, shell):
    core, window, trace, badge = shell
    assert trace == [("music", "created")]
    assert window.nav["music"].isChecked()
    core.navigate("themes")
    assert window.stack.currentWidget().text() == "themes"
    badge["value"] = "12"
    window.nav["music"].setFocus()
    qtbot.keyClick(window.nav["music"], Qt.Key.Key_Space)
    assert window.nav["music"].badge.text() == "12"
    assert window.nav["music"].badge.isVisible()
    assert window.stack.currentWidget().text() == "music"
    assert trace == [("music", "created"), ("themes", "created")]
    core.navigate("settings")
    assert window.stack.currentWidget().__class__.__name__ == "SettingsPage"


def test_nav_ring_follows_keyboard_navigation_only(qtbot, shell):
    core, window, _, _ = shell
    themes, music = window.nav["themes"], window.nav["music"]
    qtbot.waitUntil(window.isActiveWindow)
    window.change.setFocus()
    themes.setFocus(Qt.FocusReason.TabFocusReason)
    assert themes.property("keyboardFocus")
    qtbot.mouseClick(music, Qt.MouseButton.LeftButton)
    assert music.isChecked() and not music.property("keyboardFocus")
    assert not themes.property("keyboardFocus")
    core.navigate("themes")
    assert themes.isChecked() and not music.property("keyboardFocus")
    assert not themes.property("keyboardFocus")
    qtbot.keyClick(themes, Qt.Key.Key_Tab)
    assert window.focusWidget().property("keyboardFocus")


def test_window_sizes_and_compact_navigation(shell):
    _, window, _, _ = shell
    assert (window.width(), window.height()) == (1280, 800)
    assert (window.minimumWidth(), window.minimumHeight()) == (1024, 640)
    window.resize(1024, 640)
    assert window.nav["themes"].mark.isVisible()
    assert not window.nav["themes"].title.isVisible()
    assert window.nav["themes"].accessibleName() == "Themes"
    window.resize(1920, 1080)
    assert window.nav["themes"].title.isVisible()
    assert window.nav["settings"].geometry().top() > 980


def test_launch_toast_duplicate_guard_and_exit_hooks(shell, monkeypatch):
    core, window, trace, _ = shell
    now = [100.0]
    monkeypatch.setattr("edopal.ui.shell.monotonic", lambda: now[0])
    state = [RunState.CLOSED]
    monkeypatch.setattr(core.installs, "run_state", lambda: state[0])
    window.launch.click()
    assert core.installs.launched == 1
    assert window.toast.isVisible()
    assert window.toast.message.text() == "Launched (test)"
    # Even an earlier in-flight closed probe must not undo the launch grace period.
    window._poll()
    window.launch.click()
    assert core.installs.launched == 1
    assert window.launch.text() == "Starting…"
    now[0] += 13
    window._poll()
    assert window.launch.isEnabled()
    state[0] = RunState.RUNNING
    window._poll()
    assert window.launch.text() == "Running"
    assert not window.launch.isEnabled()
    state[0] = RunState.UNKNOWN
    window._poll()
    state[0] = RunState.CLOSED
    window._poll()
    window._poll()
    assert [row for row in trace if row[1] == "exited"] == [("themes", "exited"), ("music", "exited")]
    assert window.launch.isEnabled()
    assert window.poll_timer.interval() == 3000


def test_status_change_resets_run_state_and_updates_hooks(shell, tmp_path, monkeypatch):
    core, window, trace, _ = shell
    monkeypatch.setattr(core.installs, "run_state", lambda: RunState.RUNNING)
    window._poll()
    assert window.launch.text() == "Running"
    monkeypatch.setattr(core.installs, "run_state", lambda: RunState.CLOSED)
    core.installs.select(tmp_path / "other")
    assert "Ready" in window.folder_state.text()
    assert Path(window.folder_path.full_text) == tmp_path / "other"
    assert window.launch.isEnabled()
    assert ("music", "exited") not in trace
    assert ("themes", "selected") in trace
    assert ("music", "selected") in trace
    image_only = replace(core.installs.current(), launch=None)
    for selection, status in ((image_only, "Ready"), (None, "Missing")):
        core.installs._install = selection
        window.install_changed.emit(selection)
        assert status in window.folder_state.text()
        assert not window.launch.isEnabled()
        assert window.launch.toolTip()


def test_database_bootstrap_refreshes_waiting_install(shell, monkeypatch):
    core, window, trace, _ = shell
    waiting = replace(core.installs.current(), ready=False)
    core.installs._install = waiting
    window.install_changed.emit(waiting)
    assert "Waiting for databases" in window.folder_state.text()
    assert window.launch.isEnabled()
    monkeypatch.setattr(core.installs, "run_state", lambda: RunState.RUNNING)
    window._poll()
    monkeypatch.setattr(core.installs, "run_state", lambda: RunState.CLOSED)
    window._poll()
    assert "Ready" in window.folder_state.text()
    assert ("music", "exited") in trace


def test_launch_failure_and_shutdown(shell, monkeypatch):
    core, window, _, _ = shell

    def fail():
        raise OSError("Executable moved")

    monkeypatch.setattr(core.installs, "launch", fail)
    window.launch.click()
    assert "Executable moved" in window.toast.message.text()
    assert window.launch.isEnabled()
    calls = []
    monkeypatch.setattr(core.jobs, "shutdown", lambda: calls.append(1), raising=False)
    window.close()
    window.shutdown()
    assert calls == [1]
    assert not window.poll_timer.isActive()
    core.notify("after close")
    assert "after close" not in window.toast.message.text()

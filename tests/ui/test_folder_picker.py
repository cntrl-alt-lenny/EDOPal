from threading import Event, get_ident

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

from edopal.core.feature import FeatureSpec
from edopal.core.install import Install
from edopal.dev.preview import make_core
from edopal.ui.folder_picker import FolderPicker
from edopal.ui.jobs import QtJobRunner
from edopal.ui.shell import ShellWindow

LEFT = Qt.MouseButton.LeftButton


@pytest.fixture
def core(tmp_path, qapp):
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    core.jobs = QtJobRunner()
    yield core
    core.jobs.shutdown()


def open_picker(core, qtbot):
    picker = FolderPicker(core, None)
    qtbot.addWidget(picker)
    picker.open()
    return picker


def test_discovery_preselects_current_and_applies_chosen_row(core, qtbot, tmp_path, monkeypatch):
    current = core.installs.select(tmp_path / "Current")
    other = Install(tmp_path / "New", ready=False, label="New client")
    workers, notices = [], []
    select = core.installs.select

    def discover():
        workers.append(get_ident())
        return [other, current]

    def choose(root):
        workers.append(get_ident())
        return select(root)

    monkeypatch.setattr(core.installs, "discover", discover)
    monkeypatch.setattr(core.installs, "select", choose)
    core.notify = lambda text, kind: notices.append((text, kind))
    picker = open_picker(core, qtbot)
    qtbot.waitUntil(lambda: picker.listing.count() == 2)
    assert picker.listing.currentRow() == 1
    assert "Waiting for databases" in picker.listing.item(0).text()
    assert str(other.root) in picker.listing.item(0).text()
    picker.listing.setCurrentRow(0)
    with qtbot.waitSignal(picker.accepted):
        qtbot.mouseClick(picker.choose, LEFT)
    assert core.installs.current().root == other.root
    assert len(workers) == 2 and get_ident() not in workers
    assert len(notices) == 1 and notices[0][1] == "success"


@pytest.mark.parametrize("failed", [False, True], ids=["empty", "discovery-error"])
def test_browse_available_after_empty_or_failed_discovery(core, qtbot, tmp_path, monkeypatch, failed):
    if failed:

        def fail():
            raise OSError("disk offline")

        monkeypatch.setattr(core.installs, "discover", fail)
    picker = open_picker(core, qtbot)
    message = "disk offline" if failed else "No installations found"
    qtbot.waitUntil(lambda: message in picker.status.text())
    assert not picker.choose.isEnabled()
    folder = tmp_path / "Project Ignis"
    choices = iter(["", str(folder)])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: next(choices))
    qtbot.mouseClick(picker.browse, LEFT)
    assert picker.isVisible() and core.installs.current() is None
    with qtbot.waitSignal(picker.accepted):
        qtbot.mouseClick(picker.browse, LEFT)
    assert core.installs.current().root == folder


def test_invalid_selection_keeps_dialog_ready_for_another_choice(core, qtbot, tmp_path, monkeypatch):
    current = core.installs.select(tmp_path / "Current")
    select = core.installs.select

    def invalid(root):
        raise ValueError("No game here")

    monkeypatch.setattr(core.installs, "select", invalid)
    picker = open_picker(core, qtbot)
    qtbot.waitUntil(picker.choose.isEnabled)
    qtbot.mouseClick(picker.choose, LEFT)
    qtbot.waitUntil(lambda: "No game here" in picker.status.text())
    assert core.installs.current() == current
    assert picker.isVisible() and picker.choose.isEnabled() and picker.browse.isEnabled()
    monkeypatch.setattr(core.installs, "select", select)
    with qtbot.waitSignal(picker.accepted):
        qtbot.mouseClick(picker.choose, LEFT)


def test_dismiss_while_discovering_ignores_late_result(core, qtbot, tmp_path, monkeypatch):
    entered, release = Event(), Event()

    def discover():
        entered.set()
        assert release.wait(3)
        return [Install(tmp_path / "Game")]

    monkeypatch.setattr(core.installs, "discover", discover)
    picker = open_picker(core, qtbot)
    assert entered.wait(3)
    with qtbot.waitSignal(picker.rejected):
        qtbot.keyClick(picker, Qt.Key.Key_Escape)
    release.set()
    qtbot.waitUntil(lambda: not picker._discover_job.running)
    assert not picker.isVisible() and picker.listing.count() == 0
    assert core.installs.current() is None


def test_closing_parent_dismisses_picker_even_during_selection(core, qtbot, tmp_path, monkeypatch):
    entered, dismissed = Event(), Event()
    current = core.installs.select(tmp_path / "Current")

    def select(root):
        entered.set()
        assert dismissed.wait(3)
        return current

    monkeypatch.setattr(core.installs, "select", select)
    spec = FeatureSpec("fixture", "Fixture", "folder", 1, lambda _: QWidget())
    window = ShellWindow(core, [spec])
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.change, LEFT)
    picker = window.picker
    picker.finished.connect(lambda result: dismissed.set())
    qtbot.waitUntil(picker.choose.isEnabled)
    qtbot.mouseClick(picker.choose, LEFT)
    assert entered.wait(3)
    window.close()
    assert dismissed.is_set()
    assert picker.result() == QDialog.DialogCode.Rejected
    assert not picker.isVisible()

from dataclasses import replace
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from edopal.core.install import RunState
from edopal.dev.preview import make_core
from edopal.features.hdcards.controller import ViewState
from edopal.features.hdcards.page import HDCardPage


@pytest.fixture
def page(qtbot, tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    core = make_core(game, tmp_path / "packs", tmp_path / "home")
    core.notify = Mock()
    controller = Mock()
    state = ViewState(total=20, hd=17, known=19, pending=1, fields=3, can_undo=True)
    controller.subscribe.side_effect = lambda callback: callback(state)
    page = HDCardPage(core, controller)
    qtbot.addWidget(page)
    page.resize(1024, 700)
    page.show()
    return page


def test_scope_controls_and_actions(page):
    page.sync.click()
    page.controller.start_sync.assert_called_once_with(new_only=False, confirmed_closed=False)
    page.undo.click()
    page.controller.undo.assert_called_once_with()

    page.deck.click()
    page.controller.set_options.assert_called_with(True, False, False)
    page.original.click()
    page.controller.set_options.assert_called_with(True, True, False)
    page.render_state(replace(page.state, deck_only=True, original=True, new=12, need=9))
    assert page.headline.summary.text() == "HD installed for 17 of 19"
    assert "12 new cards · 9 need HD art" in page.status.text()
    page.new_action.click()
    page.controller.start_sync.assert_called_with(new_only=True, confirmed_closed=False)


def test_results_progress_and_cancel(page):
    assert page.first_sync.isVisible() and not page.table.isVisible()
    assert page.first_sync.title.text() == "No Syncs Yet"
    assert page.first_sync.cards.text() == "20"
    page.preview_state("syncing")
    assert page.table.isVisible() and not page.first_sync.isVisible()
    assert page.progress.value() == 624
    assert "3.2 cards/s" in page.status.text()
    assert page.cancel.isVisible()
    assert not page.sync.isEnabled()
    page.cancel.click()
    page.controller.cancel.assert_called_once_with()
    page.render_state(replace(page.state, done=625))
    assert not page.cancel.isEnabled()
    page.preview_state("done")
    assert "1,204 files downloaded · 3 unavailable" in page.status.text()
    assert page.undo.isEnabled()
    page.render_state(replace(page.state, phase="idle", detail="12 files restored"))
    assert page.status.isVisible()
    assert "12 files restored" in page.status.text()
    page.preview_state("no-install")
    assert not page.sync.isEnabled()
    assert "game folder" in page.headline.summary.text()
    assert page.first_sync.title.text() == "No Game Folder Selected"
    assert page.first_sync.metrics.isHidden()


def test_running_install_blocks_write(page, monkeypatch):
    monkeypatch.setattr(page.core.installs, "run_state", lambda: RunState.RUNNING)
    page.sync.click()
    page.controller.start_sync.assert_not_called()
    page.core.notify.assert_called_once()


@pytest.mark.parametrize("action", ("sync", "undo"))
def test_unknown_requires_confirmation(page, monkeypatch, action):
    monkeypatch.setattr(page.core.installs, "run_state", lambda: RunState.UNKNOWN)
    button = page.sync if action == "sync" else page.undo
    callback = page.controller.start_sync if action == "sync" else page.controller.undo
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    button.click()
    callback.assert_not_called()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    button.click()
    assert callback.call_args.kwargs["confirmed_closed"] is True


def test_confirmation_applies_to_the_checked_game(page, monkeypatch, tmp_path):
    monkeypatch.setattr(page.core.installs, "run_state", lambda: RunState.UNKNOWN)

    def confirm(*args):
        page.core.installs.select(tmp_path / "different-game")
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    page.sync.click()
    page.controller.start_sync.assert_not_called()


def test_retry_and_scrolling_accessibility(page, qtbot):
    page.preview_state("idle")
    page.retry.click()
    page.controller.start_sync.assert_called_once_with(retry_failed=True, confirmed_closed=False)
    page.rush.click()
    page.controller.set_options.assert_called_with(False, True, True)
    top, middle, bottom = page.rows
    a, b, c = (row.offset for row in page.rows)
    qtbot.waitUntil(lambda: top.offset > a and middle.offset < b and bottom.offset > c)
    before = top.offset
    qtbot.keyClick(top, Qt.Key.Key_Right)
    assert top.offset >= before + 0.24
    assert page.table.rowCount() == 3 and page.table.horizontalHeaderItem(3).text() == "Cards Downloaded"
    assert page.headline.summary.text() == "HD installed for 20,384 of 20,668"


def test_sync_panel_facts_and_grid(page):
    page.preview_state("idle")
    metric, groups, sync = page.headline, page.groups, page.sync_panel
    assert len({panel.geometry().top() for panel in (metric, groups, sync)}) == 1
    assert len({panel.height() for panel in (metric, groups, sync)}) == 1
    assert [count.text() for count in sync.counts.values()] == ["87", "197", "4,398"]
    info = [button for button in sync.findChildren(QPushButton) if button.accessibleName().startswith("About ")]
    assert len({button.toolTip() for button in info if button.toolTip()}) == 3
    switches = (page.deck, page.original, page.rush)
    assert len({(switch.geometry().left(), switch.geometry().right(), switch.height()) for switch in switches}) == 1


def test_showcase_pause_and_shift_wheel(page, qtbot):
    row = page.rows[0]
    qtbot.mouseMove(row, row.rect().center())
    qtbot.waitUntil(row.underMouse)
    before = row.offset
    qtbot.wait(row.timer.interval() * 2)
    assert row.offset == before
    local = row.rect().center()
    wheel = QWheelEvent(
        QPointF(local),
        QPointF(row.mapToGlobal(local)),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ShiftModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(row, wheel)
    assert wheel.isAccepted()
    assert row.offset == pytest.approx(before - 40 / row.stride)
    before = row.offset
    qtbot.mouseMove(page.headline, page.headline.rect().center())
    qtbot.waitUntil(lambda: row.offset > before)


def test_showcase_distinct_at_each_phase_and_width(page):
    pool = [item[0] for row in page.rows for item in row.items]
    assert len(set(pool)) == len(pool) == 18
    assert all(not pixmap.isNull() for row in page.rows for _, *pair in row.items for pixmap in pair)
    for width in (1280, 1920, 3440):
        page.resize(width, 1080)
        QApplication.processEvents()
        for row in page.rows:
            for step in range(8 * len(row.items)):
                row.offset = step / 8
                tiles = list(row.visible_tiles())
                visible = [item[0] for _, item in tiles]
                assert len(set(visible)) == len(visible)
                row.offset += len(row.items)
                assert list(row.visible_tiles()) == tiles


def test_showcase_merges_synced_cards_without_repeating_prints(page):
    output = BytesIO()
    Image.new("RGB", (8, 8)).save(output, "JPEG")
    sample = output.getvalue()
    page.render_state(
        replace(
            page.state,
            pairs=(
                ("Pot of Greed", sample, sample),
                ("Pot of greed", sample, sample),
                ("Mirror Force", sample, sample),
                ("Invalid Data", b"", sample),
            ),
        )
    )
    pool = [item[0] for row in page.rows for item in row.items]
    assert len(pool) == 19
    assert pool.count("Pot of Greed") == pool.count("Mirror Force") == 1
    assert "Pot of greed" not in pool and "Invalid Data" not in pool

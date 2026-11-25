from dataclasses import replace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

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
    state = ViewState(total=20, hd=17, fields=3, can_undo=True)
    controller.subscribe.side_effect = lambda callback: callback(state)
    page = HDCardPage(core, controller)
    qtbot.addWidget(page)
    page.resize(1024, 700)
    page.show()
    return page


def test_scope_controls_and_actions(page):
    page.hero.sync.click()
    page.controller.start_sync.assert_called_once_with(new_only=False, confirmed_closed=False)
    page.undo.click()
    page.controller.undo.assert_called_once_with()

    page.deck.click()
    page.controller.set_options.assert_called_with(True, False)
    page.original.click()
    page.controller.set_options.assert_called_with(True, True)
    page.render_state(replace(page.state, deck_only=True, original=True, new=12, need=9))
    assert page.hero.coverage.text() == "17 of 20 cards in HD · 3 field spells"
    assert page.notice.body.text() == "12 new cards · 9 need HD art"
    page.notice.action.click()
    page.controller.start_sync.assert_called_with(new_only=True, confirmed_closed=False)


def test_results_progress_and_cancel(page):
    page.preview_state("syncing")
    assert page.activity.progress.value() == 624
    assert "3.2 cards/s" in page.activity.body.text()
    assert page.activity.action.isVisible()
    assert not page.hero.sync.isEnabled()
    page.activity.action.click()
    page.controller.cancel.assert_called_once_with()
    page.render_state(replace(page.state, done=625))
    assert not page.activity.action.isEnabled()
    page.preview_state("done")
    assert page.activity.body.text() == "1,204 downloaded · 3 unavailable"
    assert page.undo.isEnabled()
    page.render_state(replace(page.state, phase="idle", detail="12 files restored"))
    assert page.activity.isVisible()
    assert page.activity.body.text() == "12 files restored"
    page.preview_state("no-install")
    assert not page.hero.sync.isEnabled()
    assert "game folder" in page.hero.coverage.text()


def test_running_install_blocks_write(page, monkeypatch):
    monkeypatch.setattr(page.core.installs, "run_state", lambda: RunState.RUNNING)
    page.hero.sync.click()
    page.controller.start_sync.assert_not_called()
    page.core.notify.assert_called_once()


@pytest.mark.parametrize("action", ("sync", "undo"))
def test_unknown_requires_confirmation(page, monkeypatch, action):
    monkeypatch.setattr(page.core.installs, "run_state", lambda: RunState.UNKNOWN)
    button = page.hero.sync if action == "sync" else page.undo
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
    page.hero.sync.click()
    page.controller.start_sync.assert_not_called()

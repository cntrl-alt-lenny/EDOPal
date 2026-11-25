import sqlite3
from contextlib import closing
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PIL import Image
from PySide6.QtCore import Qt
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
    top, bottom = page.rows
    initial = (top.offset, bottom.offset)
    qtbot.waitUntil(lambda: top.offset > initial[0] and bottom.offset < initial[1])
    before = top.offset
    qtbot.keyClick(top, Qt.Key.Key_Right)
    assert top.offset >= before + 0.24
    assert page.table.rowCount() == 3 and page.table.horizontalHeaderItem(3).text() == "Cards Downloaded"
    assert page.headline.summary.text() == "HD installed for 20,384 of 20,668"


def test_banner_and_dice_are_distinct_actions(page, qtbot, monkeypatch):
    root = page.core.installs.current().root
    expansions = root / "expansions"
    expansions.mkdir()
    code = 14087893
    with closing(sqlite3.connect(expansions / "cards.cdb")) as db, db:
        db.execute("CREATE TABLE datas (id INTEGER PRIMARY KEY, type INTEGER)")
        db.execute("CREATE TABLE texts (id INTEGER PRIMARY KEY, name TEXT, desc TEXT)")
        db.execute("INSERT INTO datas VALUES (?, ?)", (code, 0x10002))
        db.execute("INSERT INTO texts VALUES (?, ?, ?)", (code, "Book of Moon", "Flip a monster face-down."))
    image = root / "pics" / f"{code}.jpg"
    image.parent.mkdir()
    Image.new("RGB", (421, 614)).save(image)

    first = {"id": 55144522, "name": "Pot of Greed", "set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-119"}
    next_card = {"id": code, "name": "Banner Name", "set_name": "Pharaonic Guardian", "set_code": "PGD-035"}
    page.banner.display((first, b""))
    open_detail = Mock()
    monkeypatch.setattr("edopal.features.hdcards.page.show_card_detail", open_detail)
    reroll = Mock(return_value=(next_card, b""))
    monkeypatch.setattr("edopal.features.hdcards.page.load_banner", reroll)

    qtbot.mouseClick(page.banner.reroll, Qt.MouseButton.LeftButton)
    assert reroll.call_args.kwargs["previous"] == first["id"]
    open_detail.assert_not_called()
    assert page.banner.record == next_card

    qtbot.mouseClick(page.banner, Qt.MouseButton.LeftButton)
    parent, detail = open_detail.call_args.args
    assert parent is page.banner
    assert (detail.code, detail.name, detail.kind, detail.text, detail.image) == (
        code,
        "Book of Moon",
        "Spell · Quick-Play",
        "Flip a monster face-down.",
        image,
    )
    assert dict(detail.facts) == {
        "First Set": "Pharaonic Guardian (PGD-035)",
        "Card ID": str(code),
        "HD Status": "HD Installed",
    }

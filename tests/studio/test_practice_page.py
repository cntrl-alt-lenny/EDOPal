from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from config import Config
from practice.library import Library, scan
from practice.progress import Progress, read_progress
from studio.context import Installation
from studio.pages.practice import PracticePage
from studio.shell import StudioWindow
from tools.studio_practice_shots import seed_practice


def open_library(
    make_window: Callable[[], StudioWindow], game: Config, qtbot: QtBot
) -> tuple[StudioWindow, PracticePage]:
    seed_practice(Path(game.edopro_path))
    window = make_window()
    window.nav["practice"].click()
    page = window.pages["practice"]
    qtbot.waitUntil(lambda: page.state == "browse" and not page.ctx.tasks.jobs)
    return window, page


def test_opponent_controls_and_warm_visit(
    make_window: Callable, game: Config, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = open_library(make_window, game, qtbot)
    b = page.browser
    assert b.current.name == "Moonlit Knights"
    assert sum(s.count for s in b.deck.cards.sections) == 40
    b.search.setText("Moonlit Sentry")
    assert len(b.rows) == 3
    b.rule.setCurrentText("Master Rule 4")
    assert [v.name for v in b.rows] == ["Amber Tide"]
    b.level.setCurrentText("Level 1")
    assert b.empty.isVisible() and not b.pick.isEnabled()
    b.clear.click()
    b.fit.setCurrentText("Does not fit")
    b.pick.click()
    first = b.current
    b.pick.click()
    assert first != b.current and b.current in b.rows and b.pick.text() == "Re-roll opponent"
    qtbot.keyClick(b.results, Qt.Key.Key_Return)
    assert page.state == "detail"
    labels = [x.text() for x in page.detail.findChildren(QLabel)]
    assert any("Mirror Guard" in s and "forbidden" in s for s in labels)
    assert any("Add AI player" in s and b.current.name in s for s in labels)
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    b.list_choice.setCurrentText("Open League")
    assert b.empty.isVisible()
    b.fit.setCurrentText("Cannot tell")
    assert b.current.name == "Silver Chorus" and "matches" in b.fact_copy.text()
    monkeypatch.setattr(
        "studio.pages.practice.scan", lambda *args: pytest.fail("unexpected rescan")
    )
    window.nav["cards"].click()
    window.nav["practice"].click()
    assert page.browser is b and page.state == "browse"


def test_puzzle_journal_spoilers_and_undo(
    make_window: Callable, game: Config, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = open_library(make_window, game, qtbot)
    b = page.browser
    b.puzzles.click()
    assert b.collection.itemData(1) == "Tutorials"
    b.collection.setCurrentIndex(1)
    b.pick.click()
    entry = b.current
    b.open.click()
    j = page.journal

    def visible_text() -> str:
        return " ".join(x.text() for x in j.findChildren(QLabel) if x.isVisible())

    assert entry.hints[0] not in visible_text() and entry.solution not in visible_text()
    qtbot.keyClick(j.hint, Qt.Key.Key_Return)
    assert entry.hints[0] in visible_text() and entry.solution not in visible_text()
    j.solution.click()
    assert entry.solution in visible_text()
    j.note.setPlainText("Use this line again.")
    j.state.setCurrentText("Solved")
    j.played.setText("2026-02-30")
    j.save.click()
    qtbot.waitUntil(lambda: not page.saving)
    assert "YYYY-MM-DD" in j.message.text()
    monkeypatch.setattr(page.ctx, "confirm", lambda *_: False)
    assert not window.navigate("cards") and page.state == "detail"
    j.played.setText("2026-02-28")
    j.save.click()
    qtbot.waitUntil(lambda: not page.saving)
    assert read_progress(page.store, [entry])[entry.path] == Progress(
        "Solved", "Use this line again.", "2026-02-28"
    )
    assert "1 of 4 solved" in b.collection.currentText()
    j.undo.click()
    qtbot.waitUntil(lambda: not page.saving)
    assert j.value() == Progress() and "0 of 4 solved" in b.collection.currentText()
    j.state.setCurrentText("Attempted")
    j.save.click()
    qtbot.waitUntil(lambda: not page.saving)
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    b.progress_filter.setCurrentText("Attempted")
    assert b.rows == [entry]
    b.open.click()
    assert not page.journal.hint.isChecked() and not page.journal.solution.isChecked()


def test_scan_cancel_switch_retry_and_absent_files(
    make_window: Callable,
    game: Config,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gui = get_ident()
    first_game = Installation.inspect(game.edopro_path)
    cancelled = Event()

    def blocked(game_path: Path, cancel: Event, report: Callable[[str], None]) -> Library:
        assert get_ident() != gui
        report("Reading collection")
        assert cancel.wait(timeout=5)
        cancelled.set()
        raise InterruptedError

    monkeypatch.setattr("studio.pages.practice.scan", blocked)
    window = make_window()
    window.nav["practice"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.busy.detail.text() == "Reading collection")
    page.busy.cancel.click()
    qtbot.waitUntil(cancelled.is_set)
    assert page.state == "cancelled"
    window.ctx.set_installation(Installation())
    assert page.state == "empty" and page.action.isVisible()
    monkeypatch.setattr("studio.pages.practice.scan", scan)
    absent = tmp_path / "disconnected"
    window.ctx.set_installation(Installation(str(absent)))
    qtbot.waitUntil(lambda: page.state == "error")
    absent.mkdir()
    page.action.click()
    qtbot.waitUntil(lambda: page.state == "browse")
    b = page.browser
    assert b.empty.isVisible() and "WindBot.exe" in b.warning.text()
    b.puzzles.click()
    assert b.empty.isVisible() and "no puzzles folder" in b.warning.text()
    assert "Card data is not ready" in page.info.text()
    window.ctx.set_installation(first_game)
    qtbot.waitUntil(lambda: page.browser is not None and page.browser is not b)


def test_unreadable_inputs_and_corrupt_journal(
    make_window: Callable, game: Config, qtbot: QtBot
) -> None:
    window, page = open_library(make_window, game, qtbot)
    root = Path(game.edopro_path)
    (root / "lflist.conf").write_bytes(b"\xff")
    page.store.write_bytes(b"Not a journal")
    page.browser.refresh.click()
    qtbot.waitUntil(lambda: page.state == "browse")
    b = page.browser
    assert b.current.fit(0) == "Cannot tell" and "progress is unavailable" in page.info.text()
    b.puzzles.click()
    b.search.setText("Unfinished study")
    assert "could not be read" in b.fact_copy.text()
    assert not b.pick.isEnabled() and b.kicker.text() == "PROGRESS UNAVAILABLE"
    b.open.click()
    assert not page.journal.save.isEnabled()
    assert page.journal.state.currentText() == "Unavailable"
    assert page.store.read_bytes() == b"Not a journal"

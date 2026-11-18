from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from config import Config
from formats.catalogue import Catalogue
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
    b.list_choice.click()
    page.detail.search.setText("Open League")
    page.detail.use.click()
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
    make_window: Callable, game: Config, qtbot: QtBot
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
    observed = []

    def cancel_discard() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        observed.extend(button.text().replace("&", "") for button in dialog.buttons())
        dialog.reject()

    QTimer.singleShot(0, cancel_discard)
    assert not window.navigate("cards") and page.state == "detail"
    assert set(observed) == {"Cancel", "Discard journal edits"}
    j.played.setText("2026-02-28")
    j.save.click()
    qtbot.waitUntil(lambda: not page.saving)
    assert read_progress(page.store, [entry])[entry.path] == Progress(
        "Solved", "Use this line again.", "2026-02-28"
    )
    assert "1 of 4 solved" in b.collection.currentText()
    assert j.save.text() == "Saved" and not j.save.isEnabled()
    assert j.undo.text() == "Undo last change" and "previous status" in j.message.text()
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
    assert page.journal.retry.isVisible()
    assert "Save progress" not in [btn.text() for btn in page.journal.findChildren(QPushButton)]
    assert page.store.read_bytes() == b"Not a journal"
    page.store.rename(page.store.with_suffix(".damaged"))
    page.journal.retry.click()
    qtbot.waitUntil(lambda: page.journal is not None and not page.store_issue)
    assert page.journal.save.isEnabled() and page.puzzle.title == "Unfinished study"


def test_bundled_picker_links_and_warm_check(
    make_window: Callable, game: Config, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = open_library(make_window, game, qtbot)
    b = page.browser
    b.list_choice.click()
    picker = page.detail
    rows = [picker.list.item(i) for i in range(picker.list.count())]
    assert rows[0].text() == "INSTALLED IN EDOPRO" and rows[1].text() in b.library.opponents.lists
    assert sum(bool(r.data(Qt.ItemDataRole.UserRole)) for r in rows) == 125 + len(
        b.library.opponents.lists
    )
    picker.search.setText("unknown search word")
    assert picker.use.text() == "Clear search"
    picker.use.click()
    picker.search.setText("Edison")
    qtbot.keyClick(picker.list, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.state == "browse" and b.matches is not None)
    assert "No confirmed fit for Edison" in b.warning.text()
    assert b.rows[-1].fit(0) == "Cannot tell"
    first = b.current
    assert first.distance.outside > 0 and first.distance.excess > 0
    b.open.click()
    assert any(
        first.distance.describe() == field.text() for field in page.detail.findChildren(QLabel)
    )
    page.escape()
    fid = b.format_id
    monkeypatch.setattr(
        "studio.pages.practice.check_format", lambda *_: pytest.fail("unexpected warm check")
    )
    page.select_format(fid)
    assert b.current.name == first.name and page.state == "browse"
    b.open_formats.click()
    target = window.current
    qtbot.waitUntil(lambda: target.id == "formats" and target.loaded)
    assert target.selected == fid


def test_format_worker_cancel_retry_and_delayed_deep_link(
    make_window: Callable, game: Config, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_practice(Path(game.edopro_path))
    rows = Catalogue.load().rows
    fid = next(r["id"] for r in rows if r["category"] == "OCG" and "file" in r)
    called, stopped = Event(), Event()
    gui = get_ident()

    def blocked(catalogue: Catalogue, key: int, opponents: object, cancel: Event) -> None:
        assert get_ident() != gui and key == fid
        called.set()
        assert cancel.wait(timeout=5)
        stopped.set()
        raise InterruptedError()

    window = make_window()
    window.navigate("practice")
    page = window.current
    with monkeypatch.context() as patch:
        patch.setattr("studio.pages.practice.check_format", blocked)
        page.select_format(fid)
        qtbot.waitUntil(called.is_set)
        assert not page.detail.use.isEnabled()
        page.detail.back.click()
        qtbot.waitUntil(stopped.is_set)
        assert page.state == "browse" and not page.browser.matches

    def unreadable(self: Catalogue, key: int) -> None:
        raise OSError("cannot read fixture")

    with monkeypatch.context() as patch:
        patch.setattr(Catalogue, "bundled_list", unreadable)
        page.select_format(fid)
        qtbot.waitUntil(lambda: "could not be read" in page.detail.message.text())
    page.detail.use.click()
    qtbot.waitUntil(lambda: page.browser.format_id == fid)
    page.browser.open_formats.click()
    target = window.current
    qtbot.waitUntil(lambda: target.loaded)
    assert target.region == "OCG" and target.selected == fid

from collections.abc import Callable
from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

import tournament_decks
from config import Config
from studio.context import Installation
from studio.pages import decks_flow
from studio.pages.decks import DecksPage
from studio.pages.mydecks_library import Collection
from studio.shell import StudioWindow
from tools.studio_decks_screenshots import Archive, _install_preview_cards
from tournament_decks import DeckError


def open_browser(
    window: StudioWindow, monkeypatch: pytest.MonkeyPatch, archive: Archive | None = None
) -> tuple[DecksPage, Archive]:
    archive = archive or Archive()
    monkeypatch.setattr(tournament_decks, "_fetch", archive.response)
    monkeypatch.setattr(decks_flow, "_game_running", lambda _: archive.goat_open)
    window.nav["decks"].click()
    page = window.current
    assert isinstance(page, DecksPage)
    return page, archive


def test_readiness_and_installation_picker_inline(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    selected = window.ctx.installation
    window.ctx.set_installation(Installation())
    page, _ = open_browser(window, monkeypatch)
    assert page.browser.choose.hasFocus()
    assert page.browser.unavailable.isVisible()
    assert not page.browser.search.isEnabled()
    # Nothing is left running: the page starts no request without an installation.
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)

    monkeypatch.setattr("config.find_edopro_folders", lambda **_: [])
    page.browser.choose.click()
    assert window.content.currentWidget() is window.picker
    window.picker.close_picker()
    window.ctx.set_installation(Installation(selected.path))
    qtbot.waitUntil(lambda: bool(page.selection.decks))
    assert page.browser.readiness.isVisible() and page.browser.save.isEnabled()
    assert not page.browser.goat.isVisible()
    window.ctx.set_installation(selected)
    qtbot.waitUntil(lambda: bool(page.selection.decks) and not window.ctx.tasks.jobs)
    assert not page.browser.readiness.isVisible()


def test_search_paging_errors_cache_and_stale_events(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    archive = Archive()
    entered, release = Event(), Event()
    calls = []
    real_response = archive.response

    def response(url: str, *, limit: int) -> bytes:
        calls.append(url)
        if "name%3Aor%3ASlow" in url:
            entered.set()
            release.wait(5)
        return real_response(url, limit=limit)

    monkeypatch.setattr(archive, "response", response)
    page, _ = open_browser(window, monkeypatch, archive)
    b = page.browser
    qtbot.waitUntil(lambda: bool(page.selection.decks))
    assert b.next.isEnabled() and not b.previous.isEnabled()
    b.next.click()
    qtbot.waitUntil(lambda: b.event_message.title.text() == "No tournaments found")
    assert b.page_number.text() == "Page 2" and not b.next.isEnabled()
    b.previous.click()
    qtbot.waitUntil(lambda: bool(page.selection.decks))
    assert sum("page=1" in url for url in calls) == 1
    assert sum(url.endswith("/events/GFC-41") for url in calls) == 1

    try:
        for term in ("Slow", "Offline"):
            b.search.setText(term)
            qtbot.keyClick(b.search, Qt.Key.Key_Return)
            if term == "Slow":
                qtbot.waitUntil(entered.is_set)
                assert b.event_message.title.text() == "Finding tournaments…"
        qtbot.waitUntil(lambda: b.event_message.title.text() == "Format Library is unreachable")
        assert b.event_message.retry.isVisible()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert b.event_message.title.text() == "Format Library is unreachable"
    b.search.setText("Error")
    b.submit.click()
    qtbot.waitUntil(lambda: b.event_message.title.text() == "Search unavailable")
    assert b.event_message.retry.isEnabled()

    b.search.clear()
    b.submit.click()
    qtbot.waitUntil(lambda: bool(page.selection.decks))
    b.events.setFocus()
    qtbot.keyClick(b.events, Qt.Key.Key_Down)
    qtbot.waitUntil(lambda: b.standings_message.title.text() == "No public decklists")
    assert not b.save.isEnabled()


def test_keyboard_preview_and_historical_artwork_handoff(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui_thread = get_ident()
    runs: list[tuple[list[str], int]] = []

    async def sync(cfg: Config) -> None:
        runs.append((cfg.deck_paths, get_ident()))

    window = make_window(sync)
    game = Path(window.ctx.installation.path)
    _install_preview_cards(game)
    page, _ = open_browser(window, monkeypatch)
    b = page.browser
    qtbot.waitUntil(lambda: bool(page.selection.decks) and page.goat_available)
    b.standings.setFocus()
    qtbot.keyClick(b.standings, Qt.Key.Key_Down)
    assert b.standings.currentIndex().row() == 1
    assert b.selection_count.text() == "1 selected"
    qtbot.keyClick(b.standings, Qt.Key.Key_Space)
    assert b.selection_count.text() == "2 selected"
    assert not b.preview.isEnabled()
    qtbot.keyClick(b.standings, Qt.Key.Key_Space)
    qtbot.keyClick(b.standings, Qt.Key.Key_Up)
    qtbot.keyClick(b.standings, Qt.Key.Key_Return)
    preview = page.preview.contents
    qtbot.waitUntil(preview.isVisible)
    assert preview.sections.buttons[0].hint.text() == "40 cards"
    assert preview.grid.tiles.rowCount() == 17
    assert sum(card.artwork is not None for card in preview.grid.cards) == 3
    assert "monsters" in preview.types.text()
    preview.sections.buttons[2].click()
    assert preview.grid.isVisible()
    assert preview.grid.tiles.rowCount() == 4
    assert all(card.artwork is None for card in preview.grid.cards)
    qtbot.keyClick(page.preview.back, Qt.Key.Key_Escape)
    assert page.view is b and b.preview.hasFocus()

    b.goat.click()
    b.save.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    receipt = page.imports.receipt
    assert receipt.historical_changes == 8
    saved = receipt.paths[0]
    assert "(GOAT versions)" in saved.name
    assert "5001" in saved.read_text(encoding="utf-8")
    page.results.review.click()
    assert page.view is page.review
    assert "Anything goes" in decks_flow.GOAT_ROOM_SETTINGS
    assert page.review.rows.topLevelItemCount() == 4
    qtbot.keyClick(page.review.back, Qt.Key.Key_Escape)
    page.results.artwork.click()
    qtbot.waitUntil(lambda: len(runs) == 1 and not window.ctx.busy)
    assert window.current.id == "cards"
    assert runs[0][0] == [str(saved)] and runs[0][1] != ui_thread


def test_game_open_retry_then_undo_is_scoped(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    page, archive = open_browser(window, monkeypatch)
    b = page.browser
    game = Path(window.ctx.installation.path)
    existing = set((game / "deck").iterdir())
    qtbot.waitUntil(lambda: bool(page.selection.decks))
    archive.goat_open = True
    b.save.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    assert page.results.retry.isVisible()
    assert set((game / "deck").iterdir()) == existing
    archive.goat_open = False
    page.results.retry.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    assert page.results.undo.isVisible()
    page.results.back.click()
    b.recent.click()
    monkeypatch.setattr(window.ctx, "confirm", lambda *_: True)
    archive.goat_open = True
    page.results.undo.click()
    qtbot.waitUntil(
        lambda: "Close EDOPro" in page.results.notice.message.text() and not window.ctx.busy
    )
    assert page.results.retry.isVisible()
    archive.goat_open = False
    page.results.retry.click()
    qtbot.waitUntil(lambda: page.results.title.value == "Import undone")
    assert set((game / "deck").iterdir()) == existing


def test_cancel_batch_blocks_navigation_and_preserves_completed_files(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    page, archive = open_browser(window, monkeypatch)
    b = page.browser
    qtbot.waitUntil(lambda: bool(page.selection.decks))
    archive.hold = 2
    b.select_all.click()
    assert b.selection_count.text() == "8 selected"
    b.save.click()
    try:
        qtbot.waitUntil(archive.entered.is_set)
        assert page.view is page.import_view and window.ctx.busy
        assert not window.change.isEnabled() and not window.navigate("cards")
        qtbot.keyClick(page.import_view.progress.cancel, Qt.Key.Key_Escape)
        assert not page.import_view.progress.cancel.isEnabled()
    finally:
        archive.release.set()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    assert page.results.title.value == "Import stopped"
    assert len(page.imports.receipt.paths) == 1
    assert page.imports.receipt.skipped == 7
    assert window.change.isEnabled()


def test_failed_preview_retry_and_moved_deck_refuses_handoff(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    page, _ = open_browser(window, monkeypatch)
    qtbot.waitUntil(lambda: page.browser.preview.isEnabled())
    real_preview = page.library.preview
    fail = True

    def preview(deck):
        if fail:
            raise DeckError("Published list temporarily unavailable.")
        return real_preview(deck)

    monkeypatch.setattr(page.library, "preview", preview)
    page.browser.preview.click()
    qtbot.waitUntil(lambda: page.preview.message.retry.isVisible())
    fail = False
    page.preview.message.retry.click()
    qtbot.waitUntil(page.preview.contents.isVisible)
    page.preview.save.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    page.imports.receipt.paths[0].unlink()
    page.results.artwork.click()
    qtbot.waitUntil(lambda: "have moved" in page.results.notice.message.text())
    assert window.current is page and page.view is page.results


def test_last_save_keeps_artwork_and_selects_the_saved_batch(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    page, _ = open_browser(window, monkeypatch)
    browser = page.browser
    qtbot.waitUntil(browser.preview.isEnabled)
    browser.standings.topLevelItem(1).setSelected(True)
    browser.save.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    paths = set(page.imports.receipt.paths)
    assert len(paths) == 2
    page.results.back.click()
    browser.events.setCurrentItem(browser.events.topLevelItem(1))
    qtbot.waitUntil(lambda: not page.reads)
    browser.recent.click()
    assert page.results.artwork.isVisible()

    page.results.open_saved.click()
    mine = window.current
    assert mine.id == "mydecks"
    qtbot.waitUntil(lambda: isinstance(mine.view, Collection))
    view = mine.view
    assert {view.model.rows[i.row()].deck.path for i in view.rows.selectedIndexes()} == paths
    qtbot.keyClick(view.rows, Qt.Key.Key_Return)
    assert mine.view.contents.sections.buttons[0].hint.text() == "40 cards"
    assert mine.view.contents.grid.tiles.rowCount() == 17
    assert window.navigate("decks") and page.view is page.results


def test_steam_deck_lists_leave_room_for_standings_and_actions(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    window.resize(1280, 800)
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    _install_preview_cards(Path(window.ctx.installation.path))
    page, _ = open_browser(window, monkeypatch)
    browser = page.browser
    qtbot.waitUntil(lambda: browser.preview.isEnabled() and page.goat_available)
    for rows in (browser.events, browser.standings):
        qtbot.waitUntil(
            lambda rows=rows: (
                rows.viewport().rect().contains(rows.visualItemRect(rows.topLevelItem(5)))
            )
        )
    assert page.scrolls[browser].verticalScrollBar().maximum() == 0


def test_preview_can_finish_off_page_and_retry_skips_completed_saves(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    page, archive = open_browser(window, monkeypatch)
    qtbot.waitUntil(page.browser.preview.isEnabled)
    entered, release = Event(), Event()
    load = page.library.preview

    def held(deck):
        entered.set()
        release.wait(5)
        return load(deck)

    monkeypatch.setattr(page.library, "preview", held)
    page.browser.preview.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.navigate("settings")
        assert window.navigate("decks") and page.view is page.preview
    finally:
        release.set()
    qtbot.waitUntil(page.preview.contents.isVisible)
    page.preview.back.click()
    page.browser.standings.topLevelItem(1).setSelected(True)
    archive.fail = 2
    page.browser.save.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    assert page.results.retry.isVisible() and len(page.imports.receipt.paths) == 1
    first = page.imports.receipt.paths[0]
    modified = first.stat().st_mtime_ns
    archive.fail = None
    page.results.retry.click()
    qtbot.waitUntil(lambda: page.view is page.results and not window.ctx.busy)
    assert len(page.imports.receipt.paths) == 2 and first.stat().st_mtime_ns == modified

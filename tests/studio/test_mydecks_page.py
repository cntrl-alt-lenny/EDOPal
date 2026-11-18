from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from artwork_flow import valid_decks
from config import Config
from decks import DeckIndexer, Library, Sections, encode_ydke
from decks.sharing import MAX_CODE
from studio.context import Installation
from studio.pages import mydecks_copies
from studio.pages import mydecks_files as copies
from studio.pages.mydecks import MyDecksPage
from studio.pages.mydecks_checks import STATUS, FormatCheck
from studio.pages.mydecks_duplicates import DuplicatesScreen
from studio.pages.mydecks_history import HistoryScreen, MissingDecks
from studio.pages.mydecks_library import Collection
from studio.pages.mydecks_logic import change_lines, library_rows
from studio.pages.mydecks_odds import OddsScreen
from studio.pages.mydecks_widgets import Screen
from studio.shell import StudioWindow
from studio.widgets import Button
from tools.studio_screenshots_mydecks import seed_five_matches, seed_game, write_demo_deck


@pytest.fixture
def saved(
    make_window: Callable, tmp_path: Path, qtbot: QtBot
) -> Iterator[tuple[StudioWindow, MyDecksPage]]:
    config = seed_game(tmp_path / "collections")
    window = make_window()
    window.ctx.set_installation(Installation.inspect(config.edopro_path))
    window.nav["mydecks"].click()
    page = window.current
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    yield window, page
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)


def control(screen: Screen, name: str) -> Button:
    return next(b for b in screen.findChildren(Button) if b.text() == name)


def open_row(view: Collection, title: str, qtbot: QtBot) -> None:
    row = next(i for i, r in enumerate(view.model.rows) if r.deck.name == title)
    index = view.model.index(row)
    view.rows.scrollTo(index)
    qtbot.mouseClick(
        view.rows.viewport(), Qt.MouseButton.LeftButton, pos=view.rows.visualRect(index).center()
    )


def test_history_compares_restores_and_recovers_missing_deck(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, page = saved
    deck = page.lib.search("Empty Jar - Athens")[0]
    before = deck.preview.sections
    open_row(page.view, deck.name, qtbot)
    control(page.view, "History").click()
    qtbot.waitUntil(lambda: page.view.status.text() == "No previous versions yet")
    page.escape()
    page.escape()
    main = list(before.main)
    main.remove(1026)
    main.remove(1025)
    main.append(1022)
    after = Sections(tuple(main), before.extra, before.side + (1026,))
    write_demo_deck(deck.path, after)
    edited = deck.path.read_bytes()
    page.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    open_row(page.view, deck.name, qtbot)
    control(page.view, "History").click()
    history = page.view
    assert isinstance(history, HistoryScreen)
    qtbot.waitUntil(lambda: "Moved between sections" in history.report.toPlainText())
    assert history.rows.count() == 1
    assert "+1 Sangan" in history.rows.item(0).text()
    assert "−1 Cyber Jar" in history.rows.item(0).text()
    assert "1 card moved to Side" in history.rows.item(0).text()
    for heading in ("Added", "Removed", "Moved between sections"):
        assert heading in history.report.toPlainText()
    monkeypatch.setattr(copies, "_game_running", lambda _: False)
    history.name.setText("Recovered Athens")
    history.restore.click()
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    assert deck.path.read_bytes() == edited
    assert page.lib.search("Recovered Athens")[0].preview.sections == before
    deck.path.unlink()
    page.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    page.view.missing.click()
    assert isinstance(page.view, MissingDecks)
    qtbot.keyClick(page.view.rows, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: isinstance(page.view, HistoryScreen) and len(page.view.versions) == 2)
    assert "missing" in page.view.status.text()
    assert page.view.versions[0].sections == after
    assert page.store.path.parent == Path(win.ctx.cfg.config_path).parent


def test_opening_odds_tags_update_before_write_and_survive_edit(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, page = saved
    deck = page.lib.search("Empty Jar - Athens")[0]
    open_row(page.view, deck.name, qtbot)
    control(page.view, "Opening odds").click()
    odds = page.view
    assert isinstance(odds, OddsScreen)
    qtbot.waitUntil(lambda: odds.ready)
    assert odds.table.item(0, 1).text() == "—"
    odds.name.setText("Starters")
    odds.add.click()
    qtbot.waitUntil(lambda: not odds.saving and odds.group.currentText() == "Starters")
    assert odds.table.item(0, 1).text() == "0.00%"
    assert odds.table.item(2, 2).text() == "100.00%"
    odds.search.setText("Sangan")
    target = odds.filter.index(0, 0)
    odds.cards.setCurrentIndex(target)
    release = threading.Event()
    commit = page.store.set_tags

    def held(*args: object) -> object:
        release.wait(5)
        return commit(*args)

    monkeypatch.setattr(page.store, "set_tags", held)
    try:
        qtbot.keyClick(odds.cards, Qt.Key.Key_Space)
        assert odds.saving and odds.table.item(0, 1).text() == "23.72%"
        assert odds.table.item(0, 2).text() == "28.08%"
        assert not win.navigate("cards")
    finally:
        release.set()
    qtbot.waitUntil(lambda: not odds.saving)
    assert page.escape() and page.escape()
    sections = deck.preview.sections
    write_demo_deck(deck.path, Sections(sections.main + (1022,), sections.extra, sections.side))
    page.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    open_row(page.view, deck.name, qtbot)
    control(page.view, "Opening odds").click()
    qtbot.waitUntil(lambda: isinstance(page.view, OddsScreen) and page.view.ready)
    assert page.view.group.currentText() == "Starters"
    assert "3 tagged copies" in page.view.marked.text()


def test_duplicates_can_review_five_decks_and_open_folder(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, page = saved
    deck = page.lib.search("Empty Jar - Athens")[0]
    seed_five_matches(page.lib.game, deck.preview.sections)
    page.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    page.view.search.setText("Empty Jar")
    assert len(page.view.model.rows) == 5
    assert {row.match for row in page.view.model.rows} == {"Identical & similar", "Similar"}
    paths = []
    monkeypatch.setattr("studio.pages.mydecks.reveal_file", paths.append)
    open_row(page.view, deck.name, qtbot)
    control(page.view, "Similar decks").click()
    group = page.view
    assert isinstance(group, DuplicatesScreen)
    group.search.setText("Empty Jar")
    assert sum(not group.files.item(row).isHidden() for row in range(group.files.count())) == 5
    group.base.setCurrentIndex(
        next(i for i, member in enumerate(group.members) if member.name.endswith("Athens"))
    )
    group.files.setCurrentRow(
        next(i for i, member in enumerate(group.members) if member.name.endswith("Spring"))
    )
    qtbot.waitUntil(lambda: group.summary.text() == "Identical cards and sections")
    group.files.setCurrentRow(
        next(i for i, member in enumerate(group.members) if member.name.endswith("Winter"))
    )
    qtbot.waitUntil(lambda: "moved from Main to Side" in group.report.toPlainText())
    group.folder.click()
    qtbot.waitUntil(lambda: len(paths) == 1)
    assert paths[0].stem == "Empty Jar - Winter"


def test_filter_sort_search_and_open_by_keyboard(saved: tuple, qtbot: QtBot) -> None:
    win, p = saved
    lib = p.view
    assert lib.search.hasFocus()
    lib.search.setText("Empty Jar")
    assert len(lib.model.rows) == 5
    lib.folder.setCurrentText("Tournament")
    assert lib.empty.isVisible()
    lib.folder.setCurrentIndex(0)
    lib.search.clear()
    lib.sort.setCurrentText("Last changed")
    stamps = [r.deck.modified_ns for r in lib.model.rows]
    assert stamps == sorted(stamps, reverse=True)
    lib.mode.setCurrentIndex(1)
    lib.search.setText("Pot of Greed")
    assert all("Main" in r.counts for r in lib.model.rows)
    lib.folder.setCurrentText("Goat")
    row = next(r for r in lib.model.rows if r.deck.name == "Empty Jar - Athens")
    assert row.counts == "1 Main   ·   0 Extra   ·   0 Side"
    qtbot.keyClick(lib.search, Qt.Key.Key_Return)
    qtbot.keyClick(lib.rows, Qt.Key.Key_Return)
    assert p.view.contents.cards.isVisible()
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    assert p.view is lib and lib.search.text() == "Pot of Greed"


def test_deck_contents_clipboard_artwork_and_file_reveal(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, p = saved
    open_row(p.view, "Empty Jar - Athens", qtbot)
    d = next(d for d in p.lib.decks if d.name == "Empty Jar - Athens")
    view = p.view
    cards = view.contents.cards
    qtbot.waitUntil(lambda: bool(cards.sections))
    assert [row.title for row in cards.rows.rows if row.title] == [
        "Main  ·  40 cards",
        "Extra  ·  2 cards",
        "Side  ·  3 cards",
    ]
    assert [card.name for card in cards.sections[2].cards] == [
        "Buster Blader",
        "Jinzo",
        "Black Luster Soldier",
    ]
    assert cards.sections[2].type_counts == (("Monster", 3),)
    assert cards.verticalScrollBar().maximum() > 0
    control(view, "Copy code").click()
    assert QApplication.clipboard().text() == encode_ydke(d.preview.sections)
    revealed = []
    monkeypatch.setattr("studio.pages.mydecks.reveal_file", revealed.append)
    control(view, "Show file").click()
    qtbot.waitUntil(lambda: revealed == [d.path])
    running = [True, False]
    monkeypatch.setattr(copies, "_game_running", lambda _: running.pop(0))
    handoffs = []
    monkeypatch.setattr(win.ctx, "sync_deck_files", lambda *args: handoffs.append(args) or True)
    control(view, "Get HD artwork").click()
    qtbot.waitUntil(lambda: p.retry.isVisible())
    assert not handoffs
    p.retry.click()
    qtbot.waitUntil(lambda: handoffs == [(p.lib.game, (d.path,))])


def test_every_deck_checked_and_findings_drill_down(saved: tuple, qtbot: QtBot) -> None:
    win, p = saved
    p.view.check.click()
    check = p.view
    qtbot.waitUntil(lambda: bool(check.verdicts))
    assert check.results.isVisible()
    assert check.results.count() == len(p.lib.decks)
    assert {v.status for v in check.verdicts} == {"legal", "illegal", "could not verify"}
    check.filter.setCurrentText("Legal")
    for i, v in enumerate(check.verdicts):
        assert check.results.item(i).isHidden() == (v.status != "legal")
    check.filter.setCurrentIndex(0)
    i = next(i for i, d in enumerate(p.lib.decks) if d.name == "Relinquished")
    item = check.results.item(i)
    check.results.setCurrentItem(item)
    qtbot.keyClick(check.results, Qt.Key.Key_Return)
    qtbot.waitUntil(
        lambda: isinstance(p.view, FormatCheck) and p.view is not check and bool(p.view.verdicts)
    )
    assert p.view.summary.text().startswith("Could not verify")
    assert "999" in p.view.details.toPlainText()
    p.view.lists.setCurrentText("Traditional")
    qtbot.waitUntil(lambda: bool(p.view.verdicts))
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    assert p.view is check
    check.lists.setCurrentText("Traditional")
    qtbot.waitUntil(lambda: check.results.count() == len(p.lib.decks))
    assert check.lists.currentText() == "Traditional"


def test_compare_all_change_kinds_and_equal_decks(saved: tuple, qtbot: QtBot) -> None:
    _, p = saved
    p.view.compare.click()
    comp = p.view
    qtbot.waitUntil(lambda: bool(comp.hint.text()))
    comp.before.setCurrentText("Goat/Empty Jar - Athens")
    comp.after.setCurrentText("Goat/Empty Jar - Locals")
    report = comp.report.toPlainText()
    for line in (
        "Magician of Faith · 1 moved from Main to Side",
        "1 added to Main",
        "1 removed from Extra",
        "Main changed from 3 to 2",
    ):
        assert line in report
    comp.after.setCurrentIndex(comp.before.currentIndex())
    assert "two different decks" in comp.report.toPlainText()


def test_import_busy_retry_no_clobber_and_undo(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, p = saved
    lib = p.lib
    original = lib.game / "deck/Spellcasters.ydk"
    data = original.read_bytes()
    p.view.import_btn.click()
    review = p.view
    review.code.setText("broken")
    review.preview_btn.click()
    qtbot.waitUntil(lambda: "could not be read" in review.message.text())
    assert not review.save_btn.isEnabled() and "could not be read" in review.message.text()
    code = encode_ydke(Sections((1001, 1002), (), (1000,)))
    review.code.setText(code)
    review.preview_btn.click()
    qtbot.waitUntil(lambda: review.contents is not None)
    qtbot.waitUntil(lambda: bool(review.contents.cards.sections))
    assert len(review.contents.cards.sections[0].cards) == 2
    review.code.setText(code + "!")
    assert not review.save_btn.isEnabled() and review.contents is None
    review.code.setText(code)
    review.preview_btn.click()
    qtbot.waitUntil(review.save_btn.isEnabled)
    review.name.setText("Spellcasters")
    gate = threading.Event()

    def held(_: Path) -> bool:
        assert threading.current_thread() is not threading.main_thread()
        gate.wait(5)
        return True

    monkeypatch.setattr(copies, "_game_running", held)
    review.save_btn.click()
    try:
        assert win.ctx.busy and not win.change.isEnabled()
        assert not win.navigate("cards") and not p.request_close() and p.escape()
        assert "Stay on this page" in p.message.text()
    finally:
        gate.set()
    qtbot.waitUntil(lambda: p.retry.isVisible())
    assert "Close this copy" in p.message.text()
    assert not (original.parent / "Spellcasters (2).ydk").exists()
    monkeypatch.setattr(copies, "_game_running", lambda _: False)
    p.retry.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    new = original.with_name("Spellcasters (2).ydk")
    assert new in p.receipts and new.is_file() and original.read_bytes() == data
    monkeypatch.setattr(win.ctx, "confirm", lambda *_: False)
    control(p.view, "Remove saved copy").click()
    assert new.is_file()
    monkeypatch.setattr(win.ctx, "confirm", lambda *_: True)
    control(p.view, "Remove saved copy").click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection) and not p.receipts)
    assert not new.exists() and original.read_bytes() == data


def test_repair_preview_original_and_edited_copy_preserved(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, p = saved
    original = p.lib.game / "deck/Archive/Relinquished.ydk"
    data = original.read_bytes()
    open_row(p.view, "Relinquished", qtbot)
    control(p.view, "Repair a copy").click()
    assert "Morphing Jar" in p.view.message.text()
    monkeypatch.setattr(copies, "_game_running", lambda _: False)
    p.view.save_btn.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    new = next(iter(p.receipts))
    assert new.name == "Relinquished repaired.ydk" and original.read_bytes() == data
    assert "999" not in new.read_text()
    new.write_text("#main\n1001\n")
    monkeypatch.setattr(win.ctx, "confirm", lambda *_: True)
    control(p.view, "Remove saved copy").click()
    qtbot.waitUntil(lambda: p.retry.isVisible())
    assert "edits have been kept" in p.message.text() and new.read_text() == "#main\n1001\n"


def test_unreadable_and_no_verified_replacement(saved: tuple, qtbot: QtBot) -> None:
    _, p = saved
    open_row(p.view, "Damaged copy", qtbot)
    assert not any(b.text() == "Copy code" for b in p.view.findChildren(Button))
    control(p.view, "Check format").click()
    qtbot.waitUntil(lambda: bool(p.view.verdicts))
    assert p.view.verdicts[0].status == "could not verify"
    p.escape()
    p.escape()
    (p.lib.game / "mappings.json").write_text('{"mappings":[]}')
    p.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    open_row(p.view, "Relinquished", qtbot)
    control(p.view, "Repair a copy").click()
    assert not p.view.save_btn.isEnabled() and "No verified" in p.view.message.text()


def test_no_selection_not_ready_scan_error_and_empty(
    saved: tuple, qtbot: QtBot, tmp_path: Path
) -> None:
    win, p = saved
    win.ctx.set_installation(Installation())
    assert p.view.initial.text() == "Choose folder"
    win.ctx.set_installation(Installation(str(tmp_path / "unavailable"), True))
    qtbot.waitUntil(lambda: p.view.initial is not None and p.view.initial.text() == "Retry")
    game = tmp_path / "fresh"
    game.mkdir()
    win.ctx.set_installation(Installation(str(game)))
    assert p.view.initial.text() == "Check again"
    (game / "cards.cdb").touch()
    p.view.initial.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    assert p.view.empty.isVisible() and p.view.import_btn.isEnabled()


def test_old_scan_ignored_after_installation_change(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, p = saved
    gate = threading.Event()
    old = p.lib
    received: list[threading.Event] = []

    def scan(_: DeckIndexer, *, cancel: threading.Event | None = None) -> Library:
        if cancel is not None:
            received.append(cancel)
        gate.wait(5)
        return old

    monkeypatch.setattr(DeckIndexer, "scan", scan)
    p.view.refresh.click()
    try:
        qtbot.waitUntil(lambda: bool(received))
        win.ctx.set_installation(Installation())
        assert received[0].is_set()
    finally:
        gate.set()
    qtbot.waitUntil(lambda: not win.ctx.tasks.jobs)
    assert p.lib is None and p.view.initial.text() == "Choose folder"


def test_no_lists_and_long_filename_at_minimum_size(saved: tuple, qtbot: QtBot) -> None:
    win, p = saved
    (p.lib.game / "lflist.conf").unlink()
    old = p.lib.game / "deck/Spellcasters.ydk"
    name = "Spellcasters with a very long name " * 4 + "one"
    old.rename(old.with_name(name + ".ydk"))
    p.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    win.resize(960, 600)
    open_row(p.view, name, qtbot)
    assert p.view.width() <= p.scroll.viewport().width()
    control(p.view, "Check format").click()
    assert "No forbidden lists installed" in p.view.summary.text()


def test_section_counts_for_card_in_multiple_folders_and_sections(saved: tuple) -> None:
    _, p = saved
    rows = library_rows(p.lib, "magician of faith", True, Path("Goat"), 0)
    locals_deck = next(r for r in rows if r.deck.name == "Empty Jar - Locals")
    assert locals_deck.counts == "1 Main   ·   0 Extra   ·   1 Side"
    assert all(r.folder == "Goat" for r in rows)
    assert change_lines(locals_deck.deck.preview, locals_deck.deck.preview, ()) == []


def test_hundreds_of_decks_sort_filter_and_open_last(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, p = saved
    lib = p.lib
    base = next(d for d in lib.decks if d.name == "Empty Jar - Athens")
    decks = tuple(
        replace(base, path=base.path.with_name(f"Version {i:03}.ydk")) for i in range(700)
    )
    many = Library(lib.game, decks, lib.catalog, lib.banlists, ())
    monkeypatch.setattr(DeckIndexer, "scan", lambda _, **_options: many)
    p.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    view = p.view
    assert view.model.rowCount() == 700
    view.mode.setCurrentIndex(1)
    view.search.setText("Pot of Greed")
    assert view.model.rowCount() == 700
    open_row(view, "Version 699", qtbot)
    assert control(p.view, "Copy code").isEnabled()


def test_failed_save_does_not_publish_stale_preview(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, p = saved
    p.view.import_btn.click()
    review = p.view
    review.code.setText(encode_ydke(Sections((1000,), (), ())))
    review.preview_btn.click()
    qtbot.waitUntil(review.save_btn.isEnabled)
    monkeypatch.setattr(copies, "_game_running", lambda _: True)
    review.save_btn.click()
    qtbot.waitUntil(lambda: p.retry.isVisible())
    review.code.setText("a different sharing code")
    assert not p.retry.isVisible() and not review.save_btn.isEnabled()
    assert not (p.lib.game / "deck/Imported deck.ydk").exists()


def test_selected_deck_reaches_real_artwork_handoff(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced = []

    async def offline(cfg: Config) -> None:
        synced.extend(cfg.deck_paths)

    window = make_window(offline)
    game = Path(window.ctx.installation.path)
    deck_folder = game / "deck"
    nested = deck_folder / "Goat"
    nested.mkdir()
    (deck_folder / "Spellcasters.ydk").rename(nested / "Spellcasters.ydk")
    window.nav["mydecks"].click()
    page = window.current
    qtbot.waitUntil(lambda: isinstance(page.view, Collection))
    deck = page.lib.decks[0]
    assert deck.folder == Path("Goat")
    outside = game / "outside.ydk"
    outside.write_bytes(deck.path.read_bytes())
    assert not valid_decks(str(game), game, (outside,))
    open_row(page.view, deck.name, qtbot)
    monkeypatch.setattr(copies, "_game_running", lambda _: False)
    control(page.view, "Get HD artwork").click()
    cards = window.pages["cards"]
    qtbot.waitUntil(lambda: cards.state == "results")
    assert window.current is cards and synced == [str(deck.path)]


def test_oversized_and_repeated_codes_keep_existing_files(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, p = saved
    monkeypatch.setattr(copies, "_game_running", lambda _: False)
    clip = QApplication.clipboard()
    p.view.import_btn.click()
    review = p.view
    clip.setText("x" * (2 * MAX_CODE))
    review.code.paste()
    review.preview_btn.click()
    assert "too large" in review.message.text()
    assert review.contents is None and not review.save_btn.isEnabled()

    original = p.lib.game / "deck/Spellcasters.ydk"
    before = original.read_bytes()
    code = encode_ydke(Sections((1001, 1002)))
    for i in (2, 3):
        review.code.setText(code)
        review.preview_btn.click()
        qtbot.waitUntil(review.save_btn.isEnabled)
        review.name.setText(original.stem)
        assert "numbered filename" in review.note.text()
        if i == 3:
            assert "already saved in Spellcasters (2)" in review.note.text()
        review.save_btn.click()
        qtbot.waitUntil(lambda: isinstance(p.view, Collection))
        view = p.view
        selected = view.rows.currentIndex()
        assert view.model.rows[selected.row()].deck.name == f"Spellcasters ({i})"
        assert original.read_bytes() == before
        view.import_btn.click()
        review = p.view


def test_format_overview_scrolls_and_labels_each_verdict(saved: tuple, qtbot: QtBot) -> None:
    win, p = saved
    win.resize(1280, 800)
    file = p.lib.game / "deck/Archive/Damaged copy.ydk"
    for i in range(50):
        file.with_name(f"Weekend {i:02d}.ydk").write_bytes(file.read_bytes())
    p.view.refresh.click()
    qtbot.waitUntil(lambda: isinstance(p.view, Collection))
    p.view.check.click()
    check = p.view
    qtbot.waitUntil(lambda: check.results.count() >= 60)
    assert check.results.verticalScrollBar().maximum() > 0
    for status, (_, colour) in STATUS.items():
        assert status in check.counts[status].text()
        assert colour in check.counts[status].styleSheet()
    check.filter.setCurrentText("Could not verify")
    last = next(
        check.results.item(i)
        for i in reversed(range(check.results.count()))
        if not check.results.item(i).isHidden()
    )
    assert "Weekend" in last.text()
    check.results.setCurrentItem(last)
    check.results.scrollToItem(last)
    qtbot.keyClick(check.results, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: p.view is not check and bool(p.view.verdicts))
    assert p.view.summary.text() == "Could not verify"
    assert STATUS["could not verify"][1] in p.view.summary.styleSheet()
    assert "card number" in p.view.details.toPlainText()


def test_decode_can_finish_off_page_but_escape_discards_it(
    saved: tuple, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, page = saved
    entered, release = threading.Event(), threading.Event()
    decode = mydecks_copies.preview_ydke

    def held(code, catalog):
        entered.set()
        release.wait(5)
        return decode(code, catalog)

    monkeypatch.setattr(mydecks_copies, "preview_ydke", held)
    library = page.view
    library.import_btn.click()
    review = page.view
    review.code.setText(encode_ydke(Sections((1001,))))
    review.preview_btn.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.navigate("settings")
        assert window.navigate("mydecks") and page.view is review
    finally:
        release.set()
    qtbot.waitUntil(review.save_btn.isEnabled)
    entered.clear()
    release.clear()
    review.preview_btn.click()
    try:
        qtbot.waitUntil(entered.is_set)
        qtbot.keyClick(window, Qt.Key.Key_Escape)
        assert page.view is library
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert isinstance(page.view, Collection)

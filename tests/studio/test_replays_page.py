import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, current_thread, main_thread
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QLabel
from pytestqt.qtbot import QtBot

from replays import AnnotationStore, ReplayNotes, index_replays, preserve_last_replay
from studio.context import Installation
from studio.pages import replays as page_module
from studio.pages.replays_library import format_tags, last_to_save, notes_from_fields, rules
from studio.shell import StudioWindow
from studio.widgets import Button, GameClosedBlock
from tools.studio_health_replays_shots import seed_replays


def button(window: StudioWindow, name: str) -> Button:
    return next(b for b in window.current.scroll.widget().findChildren(Button) if b.text() == name)


def library(make_window: Callable, qtbot: QtBot) -> StudioWindow:
    window = make_window()
    seed_replays(Path(window.ctx.installation.path), Path(window.ctx.cfg.config_path).parent)
    window.nav["replays"].click()
    qtbot.waitUntil(lambda: window.current.state == "library")
    return window


def test_search_sort_edit_undo_export_and_reveal(
    make_window: Callable, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = library(make_window, qtbot)
    page = window.current
    window.resize(960, 600)
    page.sort.setCurrentText("Most turns")
    page.search.setText("tournament")
    assert [e.details.turns for e in page.list.records.entries] == [12, 8]
    page.search.setText("absent tag")
    assert page.no_matches.isVisible()
    button(window, "Clear search").click()
    qtbot.keyClick(page.list, Qt.Key.Key_Return)
    entry = page.selected
    assert page.state == "detail"
    qtbot.waitUntil(
        lambda: (
            page.scroll.viewport()
            .rect()
            .contains(
                page.form.name.mapTo(page.scroll.viewport(), page.form.name.rect().bottomLeft())
            )
        )
    )
    assert page.form.save.parentWidget() is page.footer
    page.form.name.setText("Final round")
    page.form.tags.setText('practice, "round, one", practice')
    page.form.notes.setPlainText("Remember turn seven.")
    assert page.form.save.isEnabled() and not page.form.export.isEnabled()
    monkeypatch.setattr(page_module, "confirm_action", lambda *_: False)
    assert not window.navigate("cards")
    qtbot.keyClick(page.form.name, Qt.Key.Key_Escape)
    assert page.state == "detail"
    page.form.save.click()
    qtbot.waitUntil(lambda: page.state == "detail" and page.form.undo.isVisible())
    assert page.form.save.text() == "Saved" and not page.form.save.isEnabled()
    store = AnnotationStore(tmp_path)
    assert store.get(entry.replay_id) == ReplayNotes(
        "Final round", "Remember turn seven.", ("practice", "round, one")
    )
    dest = tmp_path / "duel.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(dest.with_suffix("")), ""))
    page.form.export.click()
    qtbot.waitUntil(lambda: page.state == "detail")
    with ZipFile(dest) as archive:
        assert archive.read("replay.yrpX") == entry.path.read_bytes()
        assert json.loads(archive.read("notes.json"))["tags"] == ["practice", "round, one"]
    page.form.export.click()
    qtbot.waitUntil(lambda: page.state == "write_error")
    assert any("another name" in line.text() for line in page.findChildren(QLabel))
    page.escape()
    seen = []

    def reveal(path: Path) -> bool:
        assert current_thread() is not main_thread()
        seen.append(path)
        return True

    monkeypatch.setattr("desktop_helpers.reveal_file", reveal)
    page.form.reveal.click()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert seen == [entry.path]
    page.form.undo.click()
    qtbot.waitUntil(lambda: page.state == "detail" and not page.form.undo.isVisible())
    assert store.get(entry.replay_id) == entry.annotation
    page.escape()
    assert page.state == "library"


def test_game_open_retry_busy_and_preserve(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = library(make_window, qtbot)
    page = window.current
    rolling = page.last
    monkeypatch.setattr(page_module, "_game_running", lambda _: True)
    page.preserve.click()
    qtbot.waitUntil(lambda: page.state == "write_error")
    assert not window.ctx.busy and rolling.path.exists()
    assert page.scroll.widget().findChild(GameClosedBlock).retry is button(window, "Retry")
    started, release = Event(), Event()

    def settle(_: float) -> None:
        assert current_thread() is not main_thread()
        started.set()
        assert release.wait(4)

    monkeypatch.setattr("replays.shelf.sleep", settle)
    monkeypatch.setattr(page_module, "_game_running", lambda _: False)
    button(window, "Retry").click()
    try:
        qtbot.waitUntil(started.is_set)
        assert page.state == "busy" and window.ctx.busy
        assert not page.request_close() and not window.change.isEnabled()
        assert not window.navigate("cards")
        assert page.escape()
    finally:
        release.set()
    qtbot.waitUntil(lambda: page.state == "library")
    assert page.last is None and page.saved.read_bytes() == rolling.path.read_bytes()
    assert window.change.isEnabled()
    page.saved.rename(rolling.path.with_name("A duel worth keeping.yrpX"))
    button(window, "Refresh").click()
    qtbot.waitUntil(lambda: page.state == "library")
    assert page.last is None
    rolling.path.write_bytes((rolling.path.parent / "Rush rematch.yrpX").read_bytes())
    # This different duel is already saved under its own name.
    page._watch_folder()
    qtbot.waitUntil(lambda: not window.ctx.tasks.jobs)
    assert page.last is None


def test_previous_index_cannot_save_a_different_duel(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = library(make_window, qtbot)
    page = window.current
    old = page.last
    source = old.path.parent / "Side deck practice.yrpX"
    old.path.write_bytes(source.read_bytes())
    monkeypatch.setattr(page_module, "_game_running", lambda _: False)
    monkeypatch.setattr("replays.shelf.sleep", lambda _: None)
    page.preserve.click()
    qtbot.waitUntil(lambda: page.state == "write_error")
    assert not list(old.path.parent.glob("Replay-*"))
    page.escape()
    qtbot.waitUntil(lambda: page.state == "library")
    assert page.last is None


def test_empty_new_duel_unreadable_and_folder_change(
    make_window: Callable, qtbot: QtBot, tmp_path: Path
) -> None:
    window = make_window()
    ctx = window.ctx
    current = ctx.installation
    ctx.set_installation(Installation())
    window.nav["replays"].click()
    page = window.current
    assert page.state == "no_game"
    ctx.set_installation(Installation(current.path, False))
    qtbot.waitUntil(lambda: page.state == "empty")
    assert any("waiting for card data" in line.text() for line in page.findChildren(QLabel))
    page.watch.stop()
    seed_replays(Path(current.path), tmp_path)
    page._watch_folder()
    qtbot.waitUntil(lambda: page.state == "library")
    page.search.setText("Interrupted")
    index = page.list.records.index(0)
    qtbot.mouseClick(
        page.list.viewport(), Qt.MouseButton.LeftButton, pos=page.list.visualRect(index).center()
    )
    assert page.state == "detail" and page.selected.error
    assert any(
        "Check that the file is complete" in line.text() for line in page.findChildren(QLabel)
    )
    assert not any(line.text() == "Watch in EDOPro" for line in page.findChildren(QLabel))
    qtbot.waitUntil(lambda: button(window, "Show file").isVisible())
    assert not page.form.export.isVisible()
    button(window, "Technical details").click()
    qtbot.waitUntil(
        lambda: any(
            "incomplete" in line.text() and line.isVisible() for line in page.findChildren(QLabel)
        )
    )
    ctx.set_installation(Installation(str(tmp_path / "unmounted"), False))
    qtbot.waitUntil(lambda: page.state == "error")
    (tmp_path / "unmounted").mkdir()
    button(window, "Retry").click()
    qtbot.waitUntil(lambda: page.state == "empty")
    ctx.set_installation(Installation())
    assert page.state == "no_game"


def test_tags_presets_and_two_rolling_formats(tmp_path: Path) -> None:
    seed_replays(tmp_path, tmp_path)
    entries = index_replays(tmp_path)
    last = last_to_save(entries)
    assert last and rules(last) == "Master Rule 5"
    other = replace(next(e for e in entries if e.path.stem == "Rush rematch"), is_last=True)
    assert rules(other) == "Rush Duel"
    assert last_to_save((last, other)) == last
    assert last_to_save((last, replace(last, is_last=False), other)) == other
    tags = ("round, one", 'say "go"', "practice")
    assert notes_from_fields("Duel", "", format_tags(tags)).tags == tags
    assert notes_from_fields("Duel", "", "a\r\nb\nc").tags == ("a", "b", "c")


@pytest.mark.parametrize("edited", [False, True])
def test_remove_saved_copy_leaves_the_rolling_duel_alone(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, *, edited: bool
) -> None:
    window = library(make_window, qtbot)
    page = window.current
    page.watch.stop()
    original = page.last.path
    content = original.read_bytes()
    monkeypatch.setattr(page_module, "_game_running", lambda _: False)
    monkeypatch.setattr("replays.shelf.sleep", lambda _: None)
    page.preserve.click()
    qtbot.waitUntil(lambda: page.state == "library" and page.saved_copy is not None)
    saved = page.saved
    remove = button(window, "Remove saved copy")
    assert remove.isVisible() and button(window, "Show saved copy").isVisible()
    monkeypatch.setattr(page_module, "confirm_action", lambda *_: False)
    remove.click()
    assert saved.read_bytes() == content
    if edited:
        saved.write_bytes(b"keep my edits")
    confirmed = []
    monkeypatch.setattr(
        page_module, "confirm_action", lambda *args: confirmed.append(args[-1]) or True
    )
    remove.click()
    assert confirmed == ["Remove saved copy"]
    if edited:
        qtbot.waitUntil(lambda: page.state == "write_error")
        assert saved.read_bytes() == b"keep my edits"
    else:
        qtbot.waitUntil(lambda: page.state == "library" and page.preserve is not None)
        assert not saved.exists()
    assert original.read_bytes() == content


def test_existing_identical_archive_is_never_offered_for_removal(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = library(make_window, qtbot)
    page = window.current
    page.watch.stop()
    game = Path(window.ctx.installation.path)
    monkeypatch.setattr(page_module, "_game_running", lambda _: False)
    monkeypatch.setattr("replays.shelf.sleep", lambda _: None)
    existing = preserve_last_replay(game)
    assert existing is not None
    page.preserve.click()
    qtbot.waitUntil(lambda: page.state == "library" and page.saved == existing)
    assert page.saved_copy is None
    assert not any(
        button.text() == "Remove saved copy" for button in page.scroll.widget().findChildren(Button)
    )


def test_export_retry_preserves_the_selected_path(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = library(make_window, qtbot)
    page = window.current
    page.detail(next(entry for entry in page.entries if not entry.error))
    destination = Path(window.ctx.installation.path) / "My recording.zip"
    picks = []

    def choose(*_: object) -> tuple[str, str]:
        picks.append(destination)
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(page_module, "_game_running", lambda _: True)
    page.form.export.click()
    qtbot.waitUntil(lambda: page.state == "write_error")
    assert not destination.exists()
    monkeypatch.setattr(page_module, "_game_running", lambda _: False)
    button(window, "Retry").click()
    qtbot.waitUntil(lambda: page.state == "detail")
    assert destination.is_file() and picks == [destination]


def test_large_library_returns_to_the_same_replay_and_updates_in_place(
    make_window: Callable, qtbot: QtBot, tmp_path: Path
) -> None:
    window = make_window()
    window.resize(960, 600)
    game = Path(window.ctx.installation.path)
    seed_replays(game, tmp_path, extra=320)
    window.nav["replays"].click()
    page = window.current
    qtbot.waitUntil(lambda: page.state == "library")
    assert page.preserve.isVisible()
    assert page.list.records.rowCount() == 327
    last = page.list.records.index(325)
    page.list.setCurrentIndex(last)
    page.list.scrollTo(last)
    expected = page.list.records.entries[last.row()].path
    qtbot.keyClick(page.list, Qt.Key.Key_Return)
    assert page.selected.path == expected
    qtbot.keyClick(button(window, "Replays"), Qt.Key.Key_Escape)
    assert page.list.currentIndex().row() == last.row()
    assert (
        page.list.viewport()
        .rect()
        .contains(page.list.visualRect(page.list.currentIndex()).center())
    )

    page.search.setText("duel")
    page.search.setFocus()
    page.search.setCursorPosition(1)
    rolling = game / "replay" / "_LastReplay.yrpX"
    rolling.write_bytes(b"yrpX")
    page._watch_folder()
    qtbot.waitUntil(lambda: page.preserve is None)
    assert page.state == "library" and page.search.hasFocus()
    assert page.search.text() == "duel" and page.search.cursorPosition() == 1
    assert not any(
        button.text() == "Save my last duel" for button in page.scroll.widget().findChildren(Button)
    )


def test_leaving_during_index_reopens_for_the_current_installation(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    window = make_window()
    ctx = window.ctx
    first = Path(ctx.installation.path)
    seed_replays(first, tmp_path)
    started, release = Event(), Event()

    def index(game: Path, *, annotations: AnnotationStore) -> tuple:
        if game == first:
            started.set()
            assert release.wait(5)
        return index_replays(game, annotations=annotations)

    monkeypatch.setattr(page_module, "index_replays", index)
    window.nav["replays"].click()
    try:
        qtbot.waitUntil(started.is_set)
        assert window.current.state == "loading"
        assert window.navigate("cards")
        other = tmp_path / "Game 2"
        other.mkdir()
        ctx.set_installation(Installation(str(other)))
        assert window.navigate("replays")
        qtbot.waitUntil(lambda: window.current.state == "empty")
    finally:
        release.set()
    qtbot.waitUntil(lambda: not ctx.tasks.jobs)
    assert window.current.state == "empty" and window.current.preserve is None

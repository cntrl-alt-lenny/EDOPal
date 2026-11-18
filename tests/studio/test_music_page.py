from __future__ import annotations

import shutil
import struct
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event, get_ident

import miniaudio
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QHeaderView, QLabel, QTableWidget
from pytestqt.qtbot import QtBot

import music_import
import music_library as engine
from music import settings
from music_player import PreviewPlayer
from studio.context import Installation
from studio.pages import music as music_page
from studio.pages.music import MusicPage
from studio.widgets import Button
from tools.music_screenshots import recording, seed_music


def wait(qtbot: QtBot, page: MusicPage, state: str) -> None:
    qtbot.waitUntil(lambda: page.state == state and page.job is None, timeout=10000)


def button(p: MusicPage, text: str) -> Button:
    return next(
        b
        for view in (p.scroll.widget(), p.footer)
        if view
        for b in view.findChildren(Button)
        if b.text() == text
    )


@pytest.fixture
def ready(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MusicPage]:
    window = make_window()
    p = window.pages["music"]
    conf = p.game / "config/system.conf"
    conf.parent.mkdir()
    conf.write_bytes(b"enable_music = 0\r\nmusic_volume = 23\r\n")
    recording(p.game / "sound/BGM/win/Original.wav", 1)
    recording(p.root / "roses/menu/Rose garden.wav", 2)
    recording(p.root / "roses/lose/Last rose.wav", 3)
    recording(p.root / "tf5/deck/Opening hand.wav", 4)
    monkeypatch.setattr(settings, "_game_running", lambda _: False)
    monkeypatch.setattr(p, "confirm", lambda *_: True)
    window.nav["music"].click()
    wait(qtbot, p, "home")
    yield p
    qtbot.waitUntil(lambda: not p.ctx.tasks.jobs)


def game_search(p: MusicPage, qtbot: QtBot, text: str) -> None:
    p.browse.click()
    albums = p.albums
    albums.field.setText(text)
    assert len(albums.grid.albums) == 1
    qtbot.keyClick(albums.grid, Qt.Key.Key_Return)
    assert p.state == "album"


def test_game_and_track_search_cross_scene_playlist_editor(ready: MusicPage, qtbot: QtBot) -> None:
    p, v = ready, ready.albums
    assert set(p.scene_buttons) == set(engine.SCENES)
    original = (p.game / "sound/BGM/win/Original.wav").read_bytes()
    game_search(p, qtbot, "Rose garden")
    assert v.tracks.rowCount() == 2
    i = v.tracks.currentRow()
    assert v.tracks.item(i, 0).text() == "Rose garden"
    assert v.tracks.item(i, 1).text() == "Your library"
    v.tracks.cellWidget(i, 2).click()
    v.destinations["win"].click()
    wait(qtbot, p, "playlist")
    assert [t.title for t in p.data.scenes["win"].tracks] == ["Original", "Rose garden"]
    assert len(p.data.albums["roses"].tracks) == 2
    v.list.setCurrentCell(1, 0)
    v.up.click()
    wait(qtbot, p, "playlist")
    assert v.list.item(0, 0).text() == "Rose garden"
    v.remove.click()
    wait(qtbot, p, "playlist")
    assert v.list.rowCount() == 1
    v.undo.click()
    wait(qtbot, p, "playlist")
    assert v.list.rowCount() == 2
    v.clear.click()
    wait(qtbot, p, "playlist")
    assert not p.data.scenes["win"].tracks and v.restore.isEnabled()
    v.restore.click()
    wait(qtbot, p, "playlist")
    assert (p.game / "sound/BGM/win/Original.wav").read_bytes() == original
    v.undo.click()
    wait(qtbot, p, "playlist")
    assert v.list.rowCount() == 0
    button(p, "Browse albums").click()
    assert not v.only.isChecked()
    v.only.click()
    v.field.setText("openinghand")
    assert [a.game.id for a in v.grid.albums] == ["tf5"]
    qtbot.keyClick(v.grid, Qt.Key.Key_Return)
    button(p, "Use all tracks from this album").click()
    v.destinations["win"].click()
    wait(qtbot, p, "playlist")
    assert v.list.item(0, 0).text() == "Opening hand"
    v.undo.click()
    wait(qtbot, p, "playlist")
    assert v.list.rowCount() == 0


def test_running_game_retry_and_busy_lifecycle(
    ready: MusicPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = ready
    release, entered = Event(), Event()
    threads = []

    def game_open(_: Path) -> bool:
        threads.append(get_ident())
        entered.set()
        release.wait()
        return True

    monkeypatch.setattr(settings, "_game_running", game_open)
    p.toggle_music.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert p.ctx.busy and not p.hidden() and not p.ctx.navigate("cards")
        assert not p.window().change.isEnabled()
        assert "change folders" in p.progress.detail.text()
        assert get_ident() not in threads
    finally:
        release.set()
    wait(qtbot, p, "error")
    assert not settings.music_enabled(p.game)
    assert not p.ctx.busy
    monkeypatch.setattr(settings, "_game_running", lambda _: False)
    button(p, "Retry").click()
    wait(qtbot, p, "home")
    assert (
        p.game / "config/system.conf"
    ).read_bytes() == b"enable_music = 1\r\nmusic_volume = 23\r\n"
    p.toggle_music.click()
    wait(qtbot, p, "home")
    assert not settings.music_enabled(p.game)


def test_selection_reload_and_corrupt_playlist(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path
) -> None:
    p, game = ready, ready.game
    p.ctx.set_installation(Installation())
    assert p.state == "unavailable" and button(p, "Choose folder").isEnabled()
    p.ctx.set_installation(Installation(str(game)))
    assert p.state == "unavailable" and p.data is None
    with qtbot.waitSignal(p.ctx.select_installation_requested) as checked:
        button(p, "Check again").click()
    assert checked.args == [str(game)]
    folder = game / "sound/.edopro-hd-sync/win"
    folder.mkdir(parents=True)
    (folder / "selection.json").write_text("{")
    p.ctx.set_installation(Installation(str(game), True))
    wait(qtbot, p, "home")
    p.scene_buttons["win"].click()
    assert p.albums.restore.isEnabled() and p.albums.list.rowCount() == 1
    p.albums.restore.click()
    wait(qtbot, p, "error")
    button(p, "Technical details").click()
    assert any(
        "selection.json" in text.text() and text.isVisible() for text in p.findChildren(QLabel)
    )
    assert (folder / "selection.json").read_bytes() == b"{"
    p.back_button.click()
    wait(qtbot, p, "playlist")
    assert p.albums.restore.isEnabled()
    p.escape()
    recording(p.root / "tf2/deck/After refresh.wav", 9)
    button(p, "Refresh").click()
    wait(qtbot, p, "home")
    assert p.data.albums["tf2"].tracks[0].title == "After refresh"
    second = tmp_path / "Second"
    second.mkdir()
    p.ctx.set_installation(Installation(str(second), True))
    wait(qtbot, p, "home")
    assert not p.data.scenes["win"].tracks


def test_failed_and_superseded_scan(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = ready
    source = p.game
    second = tmp_path / "Other game"
    second.mkdir()
    release, entered = Event(), Event()
    read = music_page.read_library

    def blocked(path: Path, root: Path, checkpoint: Callable) -> music_page.Library:
        if path == source:
            entered.set()
            release.wait()
            raise OSError("The music folder could not be read.")
        return read(path, root, checkpoint)

    monkeypatch.setattr(music_page, "read_library", blocked)
    button(p, "Refresh").click()
    try:
        qtbot.waitUntil(entered.is_set)
        p.ctx.set_installation(Installation(str(second), True))
        wait(qtbot, p, "home")
        assert not p.data.scenes["win"].tracks
    finally:
        release.set()
    qtbot.waitUntil(lambda: not p.ctx.tasks.jobs)
    assert p.state == "home"
    p.ctx.set_installation(Installation(str(source), True))
    wait(qtbot, p, "error")
    monkeypatch.setattr(music_page, "read_library", read)
    button(p, "Retry").click()
    wait(qtbot, p, "home")
    assert p.data.scenes["win"].tracks[0].title == "Original"


def test_preview_seek_pause_and_stop_on_hide(
    ready: MusicPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = ready
    monkeypatch.setattr(p.preview, "player", PreviewPlayer(backends=[miniaudio.Backend.NULL]))
    game_search(p, qtbot, "Rose garden")
    play = p.preview
    qtbot.keyClick(play.play, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not play.pending and play.player.playing)
    qtbot.keyClick(play.slider, Qt.Key.Key_Right)
    qtbot.waitUntil(lambda: not play.pending and play.player.position >= 5)
    play.play.click()
    qtbot.waitUntil(lambda: not play.pending and not play.player.playing)
    play.play.click()
    qtbot.waitUntil(lambda: not play.pending and play.player.playing)
    p.albums.tracks.setCurrentCell(0, 0)
    qtbot.waitUntil(lambda: not play.pending and not play.player.playing)
    play.play.click()
    assert p.ctx.navigate("cards")
    qtbot.waitUntil(lambda: not play.pending and play.player.path is None)


def test_edit_folder_plan_results_and_undo(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, imp = ready, ready.imports
    folder = tmp_path / "My recordings"
    source = recording(folder / "A tune.wav", 8)
    shutil.copyfile(p.root / "tf5/deck/Opening hand.wav", folder / "B duplicate.wav")
    (folder / "C notes.txt").write_text("notes")
    (folder / "D bad.wav").write_text("not audio")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(folder))
    p.manage.click()
    imp.tools["Add a folder"].click()
    wait(qtbot, p, "review")
    assert not imp.confirm.isEnabled()
    view = imp.review_table
    note = view.item(2, 0)
    assert note.text() == "C notes.txt" and note.checkState() == Qt.CheckState.Unchecked
    assert all(view.item(2, col).flags() == Qt.ItemFlag.NoItemFlags for col in range(4))
    assert len(imp.choices()) == 3
    p.transition.setCurrentTime(p.transition.duration())
    for i, col, value in [(0, 1, "roses"), (0, 2, "menu"), (3, 1, "tf5")]:
        rect = view.visualItemRect(view.item(i, col))
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        qtbot.waitUntil(lambda: any(c.isVisible() for c in view.findChildren(QComboBox)))
        editor = next(c for c in view.findChildren(QComboBox) if c.isVisible())
        editor.setCurrentIndex(editor.findData(value))
        qtbot.keyClick(editor, Qt.Key.Key_Return)
        qtbot.waitUntil(
            lambda i=i, col=col, value=value: (
                view.item(i, col).data(Qt.ItemDataRole.UserRole) == value
            )
        )
        qtbot.waitUntil(lambda: not any(c.isVisible() for c in view.findChildren(QComboBox)))
    assert imp.confirm.isEnabled()
    assert imp.hint.text() == "3 of 3 supported files selected"
    imp.confirm.click()
    wait(qtbot, p, "results")
    assert [r.status for r in imp.results] == ["imported", "duplicate", "failed"]
    results = p.scroll.widget().findChild(QTableWidget)
    assert results.wordWrap() and results.textElideMode() == Qt.TextElideMode.ElideNone
    assert results.verticalHeader().sectionResizeMode(2) == QHeaderView.ResizeMode.ResizeToContents
    added = p.root / "roses/menu/A tune.wav"
    assert added.read_bytes() == source.read_bytes()
    button(p, "Undo import").click()
    wait(qtbot, p, "manage")
    assert not added.exists() and source.exists()
    assert (p.root / "tf5/deck/Opening hand.wav").is_file()


def test_export_into_selected_installation_checks_the_game(
    ready: MusicPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = ready
    export = p.game / "My collection"
    export.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(export))
    monkeypatch.setattr(settings, "_game_running", lambda _: True)
    p.manage.click()
    p.imports.tools["Export library"].click()
    wait(qtbot, p, "error")
    assert list(export.iterdir()) == []
    monkeypatch.setattr(settings, "_game_running", lambda _: False)
    button(p, "Retry").click()
    wait(qtbot, p, "manage")
    assert (export / "roses/menu/Rose garden.wav").exists()


def test_personal_game_add_files_and_portable_transfer(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, imp = ready, ready.imports
    p.manage.click()
    imp.tools["Create album"].click()
    imp.name.setText("Friday night")
    qtbot.keyClick(imp.name, Qt.Key.Key_Return)
    wait(qtbot, p, "album")
    key = p.albums.key
    assert p.data.albums[key].game.title == "Friday night"
    src = recording(tmp_path / "Party.wav", 10)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: ([str(src)], ""))
    button(p, "Add files").click()
    wait(qtbot, p, "review")
    assert imp.choices()[0].game == key
    imp.confirm.click()
    wait(qtbot, p, "results")
    p.escape()
    exported = tmp_path / "Portable library"
    exported.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(exported))
    imp.tools["Export library"].click()
    wait(qtbot, p, "manage")
    assert (exported / key / "game.json").exists()
    assert (exported / key / "duel/Party.wav").exists()
    recording(exported / "dm3/menu/New song.wav", 9)
    imp.tools["Import library"].click()
    wait(qtbot, p, "manage")
    assert p.data.albums["dm3"].tracks[0].title == "New song"
    data = src.read_bytes()
    name = b"yugi/sound/stream/stage1.wav"
    bank = tmp_path / "Voice.dat"
    bank.write_bytes(
        b"KCEJYUGI"
        + struct.pack("<I", 1)
        + bytes((c >> 4 | c << 4) & 255 for c in name).ljust(256, b"\0")
        + struct.pack("<III", 280, len(data), len(data))
        + data
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(bank), ""))
    imp.tools["Power of Chaos"].click()
    wait(qtbot, p, "manage")
    assert p.data.albums["pocyugi"].tracks[0].title == "Duel 1"


def test_cancel_partial_import_reports_every_selected_file(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p, imp = ready, ready.imports
    folder = tmp_path / "Forbidden Memories"
    for i in range(3):
        recording(folder / f"Take {i}.wav", 10 + i)
    imp.plan_paths([folder])
    wait(qtbot, p, "review")
    execute = music_import.execute_import
    release, entered = Event(), Event()

    def delayed(items, *, progress, **kwargs):
        def pause(n: int, total: int) -> None:
            progress(n, total)
            entered.set()
            release.wait()

        return execute(items, progress=pause, **kwargs)

    monkeypatch.setattr(music_import, "execute_import", delayed)
    imp.confirm.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert not p.hidden()
        p.progress.cancel.click()
    finally:
        release.set()
    wait(qtbot, p, "results")
    assert [r.status for r in imp.results] == ["imported", "cancelled", "cancelled"]


def test_scaled_library_loads_on_worker_and_switches_without_rescanning(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    page = window.pages["music"]
    seed_music(page.game, page.root)
    read = music_page.read_library
    entered, release = Event(), Event()

    def delayed(game: Path, root: Path, checkpoint: Callable) -> music_page.Library:
        assert get_ident() != ui_thread
        entered.set()
        release.wait()
        return read(game, root, checkpoint)

    ui_thread = get_ident()
    monkeypatch.setattr(music_page, "read_library", delayed)
    window.nav["music"].click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert page.state == "busy" and window.navigate("cards")
        assert window.navigate("music")
    finally:
        release.set()
    wait(qtbot, page, "home")
    assert page.toggle_music.mapTo(page, page.toggle_music.rect().topLeft()).y() < page.hero.y()
    assert len(list(page.root.rglob("*.wav"))) == 84
    assert len({p.parent.parent for p in page.root.rglob("*.wav")}) == 19
    assert len(page.data.albums["forbidden"].tracks) == 12
    assert len(page.data.albums) >= 69

    def unexpected(*args: object) -> None:
        pytest.fail("Switching a scene or album started a new scan")

    monkeypatch.setattr(music_page, "read_library", unexpected)
    page.browse.click()
    page.albums.field.setText("Satellite rendezvous")
    assert [album.game.id for album in page.albums.grid.albums] == ["tf5"]
    page.albums.open_game("forbidden")
    assert page.albums.tracks.rowCount() == 12
    page.open_scene("duel")
    assert page.albums.list.rowCount() == 5
    assert len({page.albums.list.item(i, 1).text() for i in range(5)}) == 5


def test_failed_process_check_is_retryable(
    ready: MusicPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(_: Path) -> bool:
        raise OSError("Process inspection unavailable")

    p = ready
    monkeypatch.setattr(settings, "_game_running", denied)
    p.toggle_music.click()
    wait(qtbot, p, "error")
    from studio.widgets import GameClosedBlock

    block = p.scroll.widget().findChild(GameClosedBlock)
    assert "could not tell whether EDOPro is open" in block.detail.text()
    assert settings.music_enabled(p.game) is False
    monkeypatch.setattr(settings, "_game_running", lambda _: False)
    block.retry.click()
    wait(qtbot, p, "home")
    assert settings.music_enabled(p.game)

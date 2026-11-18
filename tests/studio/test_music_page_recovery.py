"""Recovery controls and responsive layout in the music editor."""

from pathlib import Path

import miniaudio
import pytest
from pytestqt.qtbot import QtBot

from music import settings
from music_player import PreviewPlayer
from studio.context import Installation
from studio.pages.music import MusicPage

from .test_music_page import button, game_search, wait
from .test_music_page import ready as ready


def test_unknown_confirmation_is_one_write_and_never_overrides_running(
    ready: MusicPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studio.pages.music_sounds_controls import AudioGameCheck

    p = ready
    status = settings.GameProcessStatus.UNKNOWN
    monkeypatch.setattr("music.processes.game_process_status", lambda _: status)
    monkeypatch.setattr(settings, "_game_running", settings.is_game_running)
    p.toggle_music.click()
    wait(qtbot, p, "error")
    block = p.scroll.widget().findChild(AudioGameCheck)
    assert block.proceed.isVisible() and block.retry.text() == "Check again"
    assert settings.music_enabled(p.game) is False
    status = settings.GameProcessStatus.RUNNING
    block.proceed.click()
    wait(qtbot, p, "error")
    block = p.scroll.widget().findChild(AudioGameCheck)
    assert block.proceed.isHidden() and settings.music_enabled(p.game) is False
    status = settings.GameProcessStatus.UNKNOWN
    block.retry.click()
    wait(qtbot, p, "error")
    p.scroll.widget().findChild(AudioGameCheck).proceed.click()
    wait(qtbot, p, "home")
    assert settings.music_enabled(p.game) is True
    p.toggle_music.click()
    wait(qtbot, p, "error")
    p.scroll.widget().findChild(AudioGameCheck).back.click()
    assert p.state == "home" and settings.music_enabled(p.game) is True


def test_preview_recovers_after_automatic_null_device(
    ready: MusicPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = ready
    game_search(p, qtbot, "Rose garden")
    preview = p.preview
    preview.player = PreviewPlayer()
    real_device = miniaudio.PlaybackDevice
    attached = False

    def device(**kwargs: object) -> miniaudio.PlaybackDevice:
        output = real_device(**{**kwargs, "backends": [miniaudio.Backend.NULL]})
        if attached:
            output.backend = "Test output"
        return output

    monkeypatch.setattr(miniaudio, "PlaybackDevice", device)
    preview.play.click()
    qtbot.waitUntil(lambda: preview.notice.isVisible() and not preview.pending)
    assert preview.play.isEnabled() and p.albums.tracks.isEnabled()
    attached = True
    preview.play.click()
    qtbot.waitUntil(lambda: preview.player.playing and not preview.pending)
    assert preview.notice.isHidden()
    p.window().resize(960, 600)
    qtbot.waitUntil(lambda: preview.play.visibleRegion().contains(preview.play.rect().center()))
    preview.stop()
    qtbot.waitUntil(lambda: not preview.pending and preview.player.path is None)


def test_resizing_music_keeps_scene_and_action_row_visible(ready: MusicPage, qtbot: QtBot) -> None:
    p = ready
    for size in [(960, 600), (1280, 800), (1600, 1000), (960, 600)]:
        p.window().resize(*size)
        qtbot.waitUntil(lambda size=size: p.home_title.isVisible() == (size[1] == 600))
        assert p.hero.isHidden() == (size[1] == 600)
        if size[1] == 800:
            assert p.hero.height() <= 160
            for widget in [*p.scene_buttons.values(), p.browse, p.manage, p.toggle_music]:
                assert (
                    p.scroll.viewport()
                    .rect()
                    .contains(widget.mapTo(p.scroll.viewport(), widget.rect().bottomRight()))
                )


def test_unknown_prompt_cannot_authorize_another_installation(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studio.pages.music_sounds_controls import AudioGameCheck

    p = ready
    original = p.game / "config/system.conf"

    def unknown(_: Path) -> bool:
        raise settings.GameProcessUnknown()

    monkeypatch.setattr(settings, "_game_running", unknown)
    p.toggle_music.click()
    wait(qtbot, p, "error")
    old = p.scroll.widget().findChild(AudioGameCheck)
    other = tmp_path / "Another game"
    (other / "config").mkdir(parents=True)
    (other / "config/system.conf").write_bytes(original.read_bytes())
    p.ctx.set_installation(Installation(str(other), True))
    old.proceed.click()
    wait(qtbot, p, "home")
    assert (
        settings.music_enabled(p.game) is False
        and settings.music_enabled(original.parents[1]) is False
    )


def test_named_destructive_confirmation_defaults_to_cancel(ready: MusicPage) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from studio.pages.music_sounds_controls import confirm_change

    checked = []

    def dismiss() -> None:
        box = QApplication.activeModalWidget()
        checked.append(
            (
                box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel),
                [button.text() for button in box.buttons()],
            )
        )
        box.reject()

    QTimer.singleShot(0, dismiss)
    assert not confirm_change(ready, "Clear playlist", "Remove all tracks from this playlist?")
    assert checked[0][0] and "Clear playlist" in checked[0][1]


def test_compact_import_excludes_all_unsupported_and_keeps_action_visible(
    ready: MusicPage, qtbot: QtBot, tmp_path: Path
) -> None:
    p = ready
    p.window().resize(960, 600)
    assert p.toggle_music.property("role") == "primary"
    assert p.browse.property("role") == "secondary"
    folder = tmp_path / "Notes"
    folder.mkdir()
    for i in range(32):
        (folder / f"Note {i}.txt").write_text("notes", encoding="utf8")
    p.imports.plan_paths([folder])
    wait(qtbot, p, "review")
    assert p.imports.review_table.rowCount() == 32
    assert not p.imports.confirm.isEnabled()
    assert p.imports.hint.text() == "This folder contains no supported audio files."
    assert not p.imports.choices()
    assert p.footer.isAncestorOf(p.imports.confirm)
    assert p.scroll.verticalScrollBar().maximum() == 0
    assert p.imports.review_table.verticalScrollBar().maximum() > 0
    p.imports.review_table.scrollToBottom()
    assert p.imports.confirm.visibleRegion().contains(p.imports.confirm.rect().center())


def test_empty_album_keeps_add_files_beside_title(ready: MusicPage, qtbot: QtBot) -> None:
    from studio import theme
    from studio.pages.music_albums import AlbumHero

    p = ready
    p.window().resize(1600, 1000)
    p.albums.open_game("dm3")
    hero = p.scroll.widget().findChild(AlbumHero)
    add = button(p, "Add files")
    qtbot.waitUntil(lambda: add.visibleRegion().contains(add.rect().center()))
    assert hero.height() <= theme.TILE_LARGE
    assert add.mapTo(p, add.rect().center()).y() < p.height() // 2

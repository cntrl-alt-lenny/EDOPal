"""Retrying sound changes and previews after temporary failures."""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from studio.pages.sounds import SoundsPage
from studio.shell import StudioWindow

from .test_sounds_page import idle
from .test_sounds_page import sound_window as sound_window


def test_unknown_sound_switch_checks_both_guards_and_expires(
    sound_window: tuple[StudioWindow, SoundsPage, Path],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from music.settings import GameProcessUnknown

    _, page, root = sound_window
    path = root / "config/system.conf"
    original = path.read_bytes()

    def unknown(_: Path) -> bool:
        raise GameProcessUnknown()

    monkeypatch.setattr("music.settings._game_running", unknown)
    monkeypatch.setattr("music_library._game_running", unknown)
    page.overview.toggle.click()
    idle(qtbot, page)
    assert page.closed.proceed.isVisible() and path.read_bytes() == original
    page.closed.retry.click()
    idle(qtbot, page)
    assert page.closed.retry.text() == "Check again" and path.read_bytes() == original
    page.closed.proceed.click()
    idle(qtbot, page)
    assert path.read_bytes() == original.replace(b"enable_sound = 0", b"enable_sound = 1")
    page.overview.toggle.click()
    idle(qtbot, page)
    monkeypatch.setattr("music_library._game_running", lambda _: True)
    page.closed.proceed.click()
    idle(qtbot, page)
    assert page.closed.isVisible() and page.closed.proceed.isHidden()
    page.closed.back.click()
    assert not page.retry_action and not page.closed.isVisible()


def test_retry_clears_when_player_changes_set(
    sound_window: tuple[StudioWindow, SoundsPage, Path],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from music.settings import GameProcessUnknown

    _, page, root = sound_window
    original = (root / "sound/draw.wav").read_bytes()

    def unknown(_: Path) -> bool:
        raise GameProcessUnknown()

    monkeypatch.setattr("music.settings._game_running", unknown)
    page.cards["builtin:Thonk"].use.click()
    page.bar.apply.click()
    idle(qtbot, page)
    assert page.closed.proceed.isVisible()
    page.cards["builtin:Synth"].use.click()
    assert not page.retry_action and page.closed.isHidden()
    page.retry(confirmed_closed=True)
    assert page.write_job is None and (root / "sound/draw.wav").read_bytes() == original


def test_failed_preview_retries_on_same_control(
    sound_window: tuple[StudioWindow, SoundsPage, Path], qtbot: QtBot
) -> None:
    _, page, _ = sound_window
    button = page.cards["builtin:Thonk"].play
    button.click()
    future = page.audio.preview
    future.set_exception(
        OSError("Could not play this cue. No audio device is available for preview.")
    )
    qtbot.waitUntil(lambda: button.text() == "No audio device")
    assert not page.audio.timer.isActive() and page.overview.mix.isEnabled()
    page.audio.player.active = None
    button.click()
    assert page.audio.preview is not future and button.text() == "Stop preview"
    page.audio.preview.set_result(None)
    qtbot.waitUntil(lambda: button.text() == "Preview set")


def test_game_opens_during_confirmed_apply(
    sound_window: tuple[StudioWindow, SoundsPage, Path],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from music.settings import GameProcessStatus, is_game_running

    _, page, game = sound_window
    original = (game / "sound/draw.wav").read_bytes()
    results = iter(
        [
            GameProcessStatus.CLOSED,
            GameProcessStatus.UNKNOWN,
            GameProcessStatus.UNKNOWN,
            GameProcessStatus.RUNNING,
        ]
    )
    monkeypatch.setattr("music.settings._game_running", is_game_running)
    monkeypatch.setattr("music.processes.game_process_status", lambda _: next(results))
    page.cards["builtin:Thonk"].use.click()
    page.bar.apply.click()
    idle(qtbot, page)
    assert page.closed.proceed.isVisible()
    assert (game / "sound/draw.wav").read_bytes() == original
    page.closed.proceed.click()
    idle(qtbot, page)
    assert page.closed.isVisible() and page.closed.proceed.isHidden()
    assert (game / "sound/draw.wav").read_bytes() == original

import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, Qt
from pytestqt.qtbot import QtBot

import main
from config import Config
from studio.context import Installation
from studio.pages.cards_library import inspect_library
from studio.shell import StudioWindow


def test_guard_retry_keeps_imported_decks_and_checks_off_thread(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    threads = []
    running = True

    def check(path: Path) -> bool:
        threads.append(threading.current_thread())
        assert path == game
        return running

    async def sync(cfg: Config) -> None:
        calls.append(cfg.deck_paths)

    window = make_window(sync)
    monkeypatch.setattr("music.settings._game_running", check)
    game = Path(window.ctx.installation.path)
    deck = game / "deck/Spellcasters.ydk"
    window.navigate("decks")
    assert window.ctx.sync_deck_files(game, (deck,))
    cards = window.current
    qtbot.waitUntil(lambda: cards.state == "blocked")
    assert not calls and window.change.isEnabled()
    assert cards.guard.retry.hasFocus()
    running = False
    qtbot.keyClick(cards.guard.retry, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: cards.state == "results")
    assert calls == [[str(deck)]] and len(threads) == 2
    assert all(t is not threading.current_thread() for t in threads)


def test_check_failure_blocks_write_and_coverage_is_read_only(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = Mock(side_effect=OSError("Could not inspect processes"))
    monkeypatch.setattr("music.settings._game_running", check)
    window = make_window(main.run)
    cards = window.current
    cards.options.start.click()
    qtbot.waitUntil(lambda: cards.state == "blocked")
    assert "couldn’t check" in cards.guard.detail.text()
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert cards.state == "options"
    cards.options.coverage.click()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert check.call_count == 1 and cards.summary.first[0] == "75%"


def test_summary_transient_field_and_card_failures_and_last_run(
    make_window: Callable, qtbot: QtBot
) -> None:
    async def sync(_: Config) -> main.DownloadStats:
        stats = main.DownloadStats()
        stats.skipped = 2
        stats.record_failure(123, "One", transient=True)
        stats.record_field_failure(124, "Two", transient=True)
        return stats

    window = make_window(sync)
    cards = window.current
    qtbot.waitUntil(lambda: cards.options.values[0].text() == "75%")
    assert cards.options.values[1].text() == "1"
    cards.options.start.click()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert "14 days" not in cards.summary.note
    assert ("Downloads interrupted by a connection error", "2") in cards.summary.rows
    assert ("Images already present", "2") in cards.summary.rows
    cards.results.done.click()
    assert "Sync complete" in cards.options.last.text()
    game = window.ctx.installation
    window.ctx.set_installation(Installation())
    assert not cards.options.snapshot.isVisible()
    window.ctx.set_installation(game)
    assert "Sync complete" in cards.options.last.text()


def test_large_library_summary_skips_per_image_reads(
    game: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(510):
        (Path(game.pics_path) / f"{n}.jpg").touch()
    monkeypatch.setattr(
        main, "collect_coverage_stats", Mock(side_effect=AssertionError("deep scan"))
    )
    result = inspect_library(game, threading.Event())
    assert result.caption == "Artwork files" and result.decks == 1
    assert int(result.artwork) > 500


@pytest.mark.parametrize("size", [(960, 600), (1280, 800), (1600, 1000), (3840, 2160)])
def test_selected_rail_item_is_reachable_after_resize(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, size: tuple[int, int]
) -> None:
    window = make_window()
    window.nav["health"].click()
    window.resize(*size)
    viewport = window.rail_scroll.viewport()
    health = window.nav["health"]
    qtbot.waitUntil(
        lambda: viewport.rect().contains(health.mapTo(viewport, QPoint(0, health.height() - 1)))
    )
    assert window.nav["settings"].isVisible()
    health.setFocus()
    qtbot.keyClick(health, Qt.Key.Key_Down)
    assert window.nav["settings"].hasFocus()
    qtbot.keyClick(window.nav["settings"], Qt.Key.Key_Down)
    assert window.nav["cards"].hasFocus()
    qtbot.keyClick(window.nav["cards"], Qt.Key.Key_Return)
    assert window.current.id == "cards"


@pytest.mark.parametrize("empty_file,theme", [(False, False), (True, False), (True, True)])
def test_empty_my_decks_stops_real_engine_before_download_or_extras(
    make_window: Callable,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    empty_file: bool,
    theme: bool,
) -> None:
    window = make_window(main.run)
    cards = window.current
    game = Path(window.ctx.installation.path)
    deck = game / "deck/Spellcasters.ydk"
    if empty_file:
        deck.write_text("#main\n#extra\n", encoding="utf-8")
    else:
        deck.unlink()
    session = Mock(side_effect=AssertionError("Empty selection started HTTP"))
    install = Mock(side_effect=AssertionError("Empty selection installed a theme"))
    monkeypatch.setattr(main, "_create_http_session", session)
    monkeypatch.setattr(main.extras, "install", install)
    cards.options.scope.select(1)
    cards.settings.toggles["theme"].setChecked(theme)
    cards.start_sync()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert cards.summary.title == "No decks found — nothing was synced"
    assert not cards.results.undo.isVisible() and not cards.results.done.isVisible()
    assert cards.results.decks.isVisible() and cards.results.full_library.isVisible()
    assert not cards.ctx.state.contains(cards._history_key())
    assert cards.run_config.my_decks and cards.ctx.exit_code == 0
    install.assert_not_called()
    session.assert_not_called()
    if empty_file:
        cards.results.decks.click()
        assert window.current.id == "decks"
        assert window.navigate("cards") and cards.state == "results"

    scopes = []

    async def capture(cfg: Config) -> None:
        scopes.append(cfg.my_decks)

    monkeypatch.setattr(window.ctx, "run_coro", capture)
    cards.results.full_library.click()
    qtbot.waitUntil(lambda: cards.state == "results" and bool(scopes))
    assert scopes == [False] and cards.summary.title == "No artwork to download for this selection"


def test_leave_coverage_read_and_switch_folders_without_adopting_old_result(
    make_window: Callable, qtbot: QtBot, tmp_path: Path
) -> None:
    entered, release = threading.Event(), threading.Event()

    async def read(cfg: Config) -> None:
        assert cfg.stats
        entered.set()
        assert release.wait(5)

    window = make_window(read)
    cards = window.current
    cards.options.coverage.click()
    try:
        qtbot.waitUntil(entered.is_set)
        assert window.change.isEnabled() and window.navigate("settings")
        assert window.navigate("cards") and cards.state == "progress"
        newer = Installation(str(tmp_path / "Other"))
        window.ctx.set_installation(newer)
    finally:
        release.set()
    qtbot.waitUntil(lambda: cards.job is None)
    assert window.ctx.installation == newer and cards.state == "options"


def test_saving_new_defaults_retires_blocked_retry(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window()
    cards = window.current
    monkeypatch.setattr("music.settings._game_running", lambda _: True)
    cards.options.start.click()
    qtbot.waitUntil(lambda: cards.state == "blocked")
    window.navigate("settings")
    editor = window.current
    editor.tiles["defaults"].click()
    editor.defaults.toggles["force"].click()
    editor.defaults.done.click()
    qtbot.waitUntil(lambda: editor.section == "home")
    assert window.navigate("cards") and cards.state == "options"
    assert cards.settings.toggles["force"].isChecked() and not cards.guard.retry.isVisible()

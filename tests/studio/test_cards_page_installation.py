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
    assert "Could not check" in cards.guard.detail.text()
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
    assert ("Network errors (will retry)", "2") in cards.summary.rows
    assert ("Queued downloads skipped (file already present)", "2") in cards.summary.rows
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

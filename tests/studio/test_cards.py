from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from pytestqt.qtbot import QtBot

import main
from config import Config
from studio import theme as t
from studio.shell import StudioWindow
from tools.studio_screenshots import DemoSync


def test_real_coverage_and_settings_without_downloading(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window(main.run)
    card = window.current
    assert card.options.scope.group.checkedId() == 1
    card.options.settings.click()
    assert card.state == "settings"
    assert set(card.settings.toggles) == {
        "field_art",
        "hd_era_art",
        "theme",
        "save_report",
        "repair",
        "force",
    }
    pics = Path(window.ctx.cfg.pics_path)
    before = set(pics.rglob("*"))
    card.settings.coverage.click()
    qtbot.waitUntil(lambda: card.state == "results", timeout=6000)
    assert card.summary.title == "Artwork coverage"
    assert card.summary.first[0] == "75%"
    assert card.summary.second[0] == "62%"
    assert set(pics.rglob("*")) == before
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert card.state == "options"


def test_progress_to_failure_summary_and_report(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    fake = DemoSync()
    window = make_window(fake)
    window.resize(960, 600)
    card = window.current
    card.settings.toggles["save_report"].setChecked(True)
    card.options.start.click()
    qtbot.waitUntil(lambda: card.done == 78)
    assert card.progress.percent.text() == "62%"
    assert card.progress.detail.text() == "Syncing Dark Magician"
    window.nav["skins"].click()
    assert window.current is card
    assert not window.change.isEnabled()
    fake.release.set()
    qtbot.waitUntil(lambda: card.state == "results")
    assert card.summary.first == ("125", "Downloaded")
    assert card.summary.second == ("4", "Unavailable")
    assert ("Network errors (will retry)", "1") in card.summary.rows
    card.results.failures.click()
    assert card.results.list.isVisible()
    assert card.results.model.rowCount() == 4
    viewport = card.views["results"].viewport()
    qtbot.waitUntil(
        lambda: viewport.rect().contains(card.results.list.mapTo(viewport, QPoint(0, 0)))
    )
    assert any("Will retry" in item for item in card.results.model.stringList())
    assert Path(card.run_config.report_path).is_file()
    assert (
        Path(card.run_config.report_path).read_text(encoding="utf-8").splitlines()[0]
        == "EDOPal Report"
    )
    assert str(card.run_config.report_path) in card.summary.full_note
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert card.state == "results" and not card.results.list.isVisible()
    card.results.done.click()
    assert card.state == "options"
    assert window.change.isEnabled()


def test_escape_cancels_and_next_run_gets_a_new_event(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    fake = DemoSync()
    window = make_window(fake)
    card = window.current
    card.options.start.click()
    qtbot.waitUntil(lambda: card.done == 78)
    previous = card.job.cancel_event
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: card.state == "results")
    assert previous.is_set()
    assert card.summary.title == "Sync cancelled"
    card.results.done.click()
    fake.release.set()
    card.options.start.click()
    assert not card.job.cancel_event.is_set()
    qtbot.waitUntil(lambda: card.state == "results")
    assert card.summary.title == "Sync complete"


def test_deck_handoff_is_scoped_and_validated_off_thread(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    configs = []

    async def sync(cfg: Config) -> None:
        configs.append(cfg)

    window = make_window(sync)
    card = window.current
    game = Path(window.ctx.installation.path)
    deck = game / "deck/Spellcasters.ydk"
    card.settings.toggles["force"].setChecked(True)
    assert window.navigate("decks")
    assert window.ctx.sync_deck_files(game, (deck,))
    qtbot.waitUntil(lambda: card.state == "results")
    assert window.current is card
    assert configs[0].deck_paths == [str(deck)]
    assert not configs[0].force
    assert card.sync_deck_files(game, (game / "cards.cdb",))
    qtbot.waitUntil(lambda: card.state == "results")
    assert len(configs) == 1
    assert card.summary.title == "Something went wrong"
    card.results.done.click()
    card.options.start.click()
    qtbot.waitUntil(lambda: card.state == "results")
    assert configs[-1].deck_paths == [] and configs[-1].force


def test_backend_folder_request_and_system_exit(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    async def sync(cfg: Config) -> None:
        assert cfg.folder_picker(cfg.edopro_path) is None
        raise main.SyncInputError("No readable cards")

    window = make_window(sync)
    window.ctx.choose_folder = lambda _: None
    window.current.options.start.click()
    qtbot.waitUntil(lambda: window.current.state == "results")
    assert window.ctx.exit_code == 1
    assert window.current.summary.note == "No readable cards"


def test_window_close_waits_for_cancelled_worker_to_finish(
    make_window: Callable[..., StudioWindow], qtbot: QtBot, tmp_path
) -> None:
    import asyncio
    import threading

    entered = threading.Event()
    finished = tmp_path / "committed.txt"

    async def write_partial(cfg: Config) -> main.DownloadStats:
        entered.set()
        while not cfg.cancel_event.is_set():
            await asyncio.sleep(0.01)
        finished.write_text("partial", encoding="utf-8")
        return main.DownloadStats()

    window = make_window(write_partial)
    window.current.options.start.click()
    qtbot.waitUntil(entered.is_set)
    assert not window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert finished.read_text(encoding="utf-8") == "partial"


def test_burst_progress_keeps_counts_at_phase_boundaries(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    async def many_cards(cfg: Config) -> main.DownloadStats:
        first = cfg.gui_progress.add_task("Card artwork", 15000)
        for i in range(15000):
            cfg.gui_progress.update(first, description=str(i))
            cfg.gui_progress.advance(first)
        second = cfg.gui_progress.add_task("Field art", 3)
        cfg.gui_progress.advance(second, 3)
        return main.DownloadStats()

    window = make_window(many_cards)
    card = window.current
    card.options.start.click()
    qtbot.waitUntil(lambda: card.state == "results")
    assert card.progress.phase.text() == "Field art"
    assert (card.done, card.total) == (3, 3)


def test_progress_banner_starts_at_the_page_inset(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.resize(1600, 1000)
    card = window.current
    card.options.start.click()
    qtbot.waitUntil(lambda: card.done == 78)
    assert card.banner.y() == t.XXL
    assert card.progress.y() == card.banner.geometry().bottom() + 1 + t.LG
    card.cancel()
    qtbot.waitUntil(lambda: card.state == "results")

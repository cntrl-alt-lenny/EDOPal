import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

import config
import main
from artwork_flow import record_artwork_change
from config import Config
from studio.pages.cards_undo import has_restore, remember_changes, restore_artwork


class OfflineImages:
    status = 200
    headers = {}

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.content = self

    def get(self, *args: object, **kwargs: object) -> "OfflineImages":
        return self

    async def __aenter__(self) -> "OfflineImages":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def iter_chunked(self, _: int) -> AsyncIterator[bytes]:
        yield self.image


def test_real_sync_and_restore_including_extras(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = make_window(main.run)
    cards = window.current
    cfg = window.ctx.cfg
    game = Path(cfg.edopro_path)
    pic = game / "pics/1001.jpg"
    old = pic.read_bytes()
    pic.write_bytes(b"original")
    field = game / "pics/field/1001.jpg"
    field.unlink()
    monkeypatch.setattr(main, "_create_http_session", lambda **_: OfflineImages(old))
    monkeypatch.setattr(window.ctx, "confirm", lambda *_: True)
    cards.settings.toggles["force"].setChecked(True)
    cards.settings.toggles["theme"].setChecked(True)
    cards.options.start.click()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert cards.summary.title == "Sync complete", cards.summary.note
    qtbot.waitUntil(cards.results.undo.isVisible)
    assert has_restore(cfg)
    assert pic.read_bytes() == old and field.is_file()
    assert any((game / "textures").rglob("*.png"))
    monkeypatch.setattr("music.settings._game_running", lambda _: True)
    cards.results.undo.click()
    qtbot.waitUntil(lambda: cards.state == "blocked")
    assert pic.read_bytes() == old
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    cards.guard.retry.click()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert cards.summary.title == "Previous artwork restored"
    assert pic.read_bytes() == b"original" and not field.exists()
    assert not any(p.is_file() for p in (game / "textures").rglob("*"))
    assert not has_restore(cfg)


def test_undo_preserves_later_edits_and_retries_after_partial_restore(game: Config) -> None:
    pics = Path(game.pics_path)
    one, two, three = (pics / f"{cid}.jpg" for cid in (1001, 2001, 3001))
    old = one.read_bytes()
    with remember_changes(game):
        for path in (one, two, three):
            record_artwork_change(path, b"new")
            path.write_bytes(b"new")
    with remember_changes(game):
        pass
    two.write_bytes(b"player edit")
    three.unlink()  # Already restored by an interrupted undo.
    folder = os.path.normcase(game.edopro_path)
    assert config.save_setting(
        game.config_path, "era_art_applied", {folder: {"1001": "hd-v2", "1002": "original-v1"}}
    )
    skipped = []
    assert restore_artwork(game, skipped=skipped) == 1
    assert skipped == ["pics/2001.jpg"] and has_restore(game)
    assert one.read_bytes() == old and two.read_bytes() == b"player edit"
    one.write_bytes(b"later edit to an already restored file")
    two.write_bytes(b"new")
    skipped.clear()
    assert restore_artwork(game, skipped=skipped) == 1
    assert skipped == [] and not has_restore(game)
    assert one.read_bytes().startswith(b"later edit") and not two.exists()
    assert config._load_config_file(game.config_path)["era_art_applied"] == {
        folder: {"1002": "original-v1"}
    }


def test_partial_run_and_installation_boundaries(game: Config, tmp_path: Path) -> None:
    addition = Path(game.pics_path) / "2011.jpg"
    with pytest.raises(RuntimeError, match="cancel"), remember_changes(game):
        record_artwork_change(addition, b"partial")
        addition.write_bytes(b"partial")
        raise RuntimeError("cancel")
    with pytest.raises(OSError), remember_changes(game):
        record_artwork_change(tmp_path / "external.jpg", b"wrong folder")
    assert restore_artwork(game) == 1
    assert not addition.exists()


def test_refused_first_write_retains_previous_undo_and_offers_retry(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studio.pages.cards_undo import ArtworkStorageError

    window = make_window(main.run)
    page = window.current
    cfg = window.ctx.cfg
    prior = Path(cfg.pics_path) / "54321.jpg"
    with remember_changes(cfg):
        record_artwork_change(prior, b"previous")
        prior.write_bytes(b"previous")

    async def refuse(*args: object, **kwargs: object) -> None:
        raise ArtworkStorageError("read-only test installation")

    original = main.download_card
    monkeypatch.setattr(main, "download_card", refuse)
    image = (Path(cfg.pics_path) / "1000.jpg").read_bytes()
    monkeypatch.setattr(main, "_create_http_session", lambda **_: OfflineImages(image))
    page.settings.toggles["force"].setChecked(True)
    page.settings.toggles["field_art"].setChecked(False)
    page.options.start.click()
    qtbot.waitUntil(lambda: page.state == "results" and page.results.undo.isVisible())
    assert page.summary.title == "Couldn't save artwork"
    assert main.STORAGE_MESSAGE in page.summary.note
    assert page.summary.first == ("0", "Downloaded")
    assert page.results.retry.isVisible() and page.results.retry.text() == "Retry"
    monkeypatch.setattr(main, "download_card", original)
    page.results.retry.click()
    qtbot.waitUntil(lambda: page.state == "results")
    assert page.summary.title == "Sync complete" and page.run_config.my_decks


def test_network_retry_requests_only_the_failed_face_and_field(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def interrupted(_: Config) -> main.DownloadStats:
        stats = main.DownloadStats()
        stats.ok_hd = 2
        stats.record_failure(1001, "Card one", transient=True)
        stats.record_field_failure(1002, "Card two", transient=True)
        return stats

    window = make_window(interrupted)
    page = window.current
    page.settings.toggles["force"].setChecked(True)
    page.options.start.click()
    qtbot.waitUntil(lambda: page.state == "results")
    assert page.summary.first == ("2", "Downloaded")
    assert page.summary.title == "2 downloads need another try"
    assert page.results.retry.text() == "Retry 2 downloads"
    calls = []

    async def face(session: object, card_id: int, *args: object, **kwargs: object) -> None:
        calls.append(("face", card_id))

    async def field(session: object, card_id: int, *args: object, **kwargs: object) -> None:
        calls.append(("field", card_id))

    monkeypatch.setattr(window.ctx, "run_coro", main.run)
    monkeypatch.setattr(main, "download_card", face)
    monkeypatch.setattr(main, "download_field_art", field)
    monkeypatch.setattr(main, "_create_http_session", lambda **_: OfflineImages(b""))
    monkeypatch.setattr("music.settings._game_running", lambda _: True)
    page.results.retry.click()
    qtbot.waitUntil(lambda: page.state == "blocked")
    assert not calls
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    page.guard.retry.click()
    qtbot.waitUntil(lambda: page.state == "results")
    assert sorted(calls) == [("face", 1001), ("field", 1002)]


def test_journal_finish_error_shows_storage_outcome(
    make_window: Callable, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artwork_flow import STORAGE_MESSAGE, ArtworkStorageError
    from studio.pages.cards_undo import _Journal

    async def partial_sync(_: Config) -> main.DownloadStats:
        stats = main.DownloadStats()
        stats.ok_hd = 1
        return stats

    def refused(_: _Journal) -> None:
        raise ArtworkStorageError("101.jpg is in use")

    window = make_window(partial_sync)
    cards = window.current
    monkeypatch.setattr(_Journal, "finish", refused)
    cards.options.start.click()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert cards.summary.title == "Couldn't save artwork"
    assert STORAGE_MESSAGE in cards.summary.note and cards.summary.first[0] == "1"


def test_partial_undo_shows_edited_file_and_keeps_undo_available(
    make_window: Callable, qtbot: QtBot
) -> None:
    window = make_window()
    cards = window.current
    cfg = window.ctx.cfg
    pics = Path(cfg.pics_path)
    one, two = pics / "92001.jpg", pics / "92002.jpg"
    with remember_changes(cfg) as writer:
        writer.publish(one, b"installed")
        writer.publish(two, b"installed")
    two.write_bytes(b"player edit")
    cards._restore()
    qtbot.waitUntil(lambda: cards.state == "results")
    assert "1 file change undone" in cards.summary.note
    assert "pics/92002.jpg" in cards.summary.note
    # Undo availability follows the library snapshot, which is re-read on a worker.
    qtbot.waitUntil(cards.results.undo.isVisible)
    assert not one.exists() and two.read_bytes() == b"player edit"

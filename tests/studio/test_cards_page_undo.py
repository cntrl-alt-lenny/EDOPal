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
    assert cards.results.undo.isVisible() and has_restore(cfg)
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
    one, two = pics / "1001.jpg", pics / "2001.jpg"
    old = one.read_bytes()
    with remember_changes(game):
        for path in (one, two):
            record_artwork_change(path, b"new")
            path.write_bytes(b"new")
    with remember_changes(game):
        pass
    two.write_bytes(b"player edit")
    with pytest.raises(ValueError, match="has changed"):
        restore_artwork(game)
    assert one.read_bytes() == b"new" and two.read_bytes() == b"player edit"
    two.write_bytes(b"new")
    one.write_bytes(old)
    folder = os.path.normcase(game.edopro_path)
    assert config.save_setting(
        game.config_path, "era_art_applied", {folder: {"1001": "hd-v2", "1002": "original-v1"}}
    )
    assert restore_artwork(game) == 1
    assert one.read_bytes() == old and not two.exists()
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

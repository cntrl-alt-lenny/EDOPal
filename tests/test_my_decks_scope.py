import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import main
from artwork_flow import sync_summary
from config import Config


def test_mixed_extension_decks(tmp_path: Path) -> None:
    target = tmp_path / "deck/AI"
    target.mkdir(parents=True)
    (target / "Battle.YdK").write_text("123\n")
    cfg = Config(
        ["--config", str(tmp_path / "config.json"), "--decks-folder", str(tmp_path / "deck")]
    )
    assert main.collect_deck_filter_ids(cfg) == {123}
    assert main._count_deck_files(str(tmp_path / "deck")) == 1


@pytest.mark.parametrize("empty_deck", [True, False])
@pytest.mark.parametrize("database", [True, False])
def test_empty_my_decks_stops_before_full_index_or_download(
    tmp_path: Path, empty_deck: bool, database: bool
) -> None:
    if database:
        (tmp_path / "cards.cdb").touch()
    if empty_deck:
        (tmp_path / "deck").mkdir()
        (tmp_path / "deck/empty.ydk").write_text("#main\n")
    cfg = Config(
        [
            "--config",
            str(tmp_path / "config.json"),
            "--edopro-path",
            str(tmp_path),
            "--my-decks",
            "--quiet",
        ]
    )
    notices = []
    cfg.notice_sink = notices.append
    with (
        patch.object(main, "get_db_files", side_effect=AssertionError("started discovery")),
        patch.object(main, "_create_http_session", side_effect=AssertionError("opened network")),
    ):
        assert asyncio.run(main.run(cfg)) is None
    assert cfg.idle_summary[0] == (
        "No cards found in your decks — nothing to sync"
        if empty_deck
        else "No decks found — nothing to sync"
    )
    assert "Full library" in cfg.idle_summary[1]
    assert notices and not (tmp_path / "pics").exists()
    summary = sync_summary(cfg, {"stats": None}, False, False, False)
    assert (summary.title, summary.note) == cfg.idle_summary
    assert summary.first == ("0", "Downloaded")


def test_replacement_installation_uses_its_own_decks(tmp_path: Path) -> None:
    original = tmp_path / "first"
    (original / "deck").mkdir(parents=True)
    (original / "deck/battle.YDK").write_text("123")
    replacement = tmp_path / "second"
    replacement.mkdir()
    (replacement / "cards.cdb").touch()
    cfg = Config(
        ["--config", str(tmp_path / "config.json"), "--edopro-path", str(original), "--my-decks"]
    )
    cfg.folder_picker = lambda _: str(replacement)
    with patch.object(main, "scan_databases", side_effect=AssertionError("used first game's deck")):
        assert asyncio.run(main.run(cfg)) is None
    assert cfg.edopro_path == str(replacement)
    assert cfg.idle_summary[0] == "No decks found — nothing to sync"

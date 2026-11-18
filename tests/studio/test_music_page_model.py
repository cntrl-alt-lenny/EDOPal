from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

import music_library as engine
from music.importing import ImportItem
from studio.pages.music_model import change_order, match_description, needs_game, read_library
from studio.pages.music_receipt import Receipt
from tools.music_screenshots import recording


def test_snapshot_filters_series_aliases_and_all_recording_titles(tmp_path: Path) -> None:
    game, root = tmp_path / "Game", tmp_path / "Library"
    game.mkdir()
    first = recording(root / "tf5/menu/Ace pilot.wav", 4)
    duplicate = root / "tf5/deck/Version two.wav"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(first.read_bytes())
    snapshot = read_library(game, root, lambda: None)
    with patch.object(Path, "open", side_effect=AssertionError("A cached search read from disk")):
        for query in ("TagForce5", "acepilot", "Version two"):
            albums = snapshot.browse(query, "Tag Force", True)
            assert [a.game.id for a in albums] == ["tf5"] and len(albums[0].tracks) == 1
        assert not snapshot.browse("acepilot", "Console")
        assert any(not a.tracks for a in snapshot.browse(""))
        assert all(a.tracks for a in snapshot.browse("", has_music=True))


def test_stale_playlist_and_cancelled_staging_leave_originals(tmp_path: Path) -> None:
    root, game = tmp_path / "Library", tmp_path / "Game"
    source = recording(game / "sound/BGM/menu/Original.wav", 1)
    new = engine.Track("tf5", "New", recording(root / "tf5/lose/New.wav", 2), "lose")
    before = source.read_bytes()
    (existing,) = engine.playlist(game, "menu", root=root)
    assert change_order((existing, new), 0, 1) == [new, existing]
    assert change_order((existing, new), 0, -1) == [existing, new]
    with pytest.raises(ValueError, match="playlist changed"):
        engine.set_playlist(game, "menu", [new], root=root, expected=[])

    def stop(done: int, total: int) -> None:
        if done == total:
            raise InterruptedError

    with pytest.raises(InterruptedError):
        engine.set_playlist(
            game, "menu", [new], root=root, expected=[existing.digest], progress=stop
        )
    assert source.read_bytes() == before and not engine.can_restore(game, "menu")


def test_reviewable_assignments_and_duplicate_scope(tmp_path: Path) -> None:
    item = ImportItem(tmp_path / "Take.wav")
    assert match_description(item, False) == "Check album" and needs_game(item, False)
    duplicate = replace(item, in_library=True, duplicate=True)
    assert match_description(duplicate, False) == "Found in library"
    assert not needs_game(duplicate, False) and needs_game(duplicate, True)
    assert match_description(replace(item, path=tmp_path / "Notes.txt"), False) == "Unsupported"
    assert not needs_game(replace(item, error="Unreadable recording"), True)


def test_undo_keeps_changed_files_and_other_recordings(tmp_path: Path) -> None:
    root = tmp_path / "library"
    added = engine.create_game("The band", root=root)
    receipt = Receipt(root, "game creation")
    receipt.record(root / added.id / "game.json")
    take = recording(root / added.id / "duel/Tune.wav", 2)
    with pytest.raises(ValueError, match="other files"):
        receipt.undo()
    audio = Receipt(root, "import")
    audio.record(take)
    original = take.read_bytes()
    take.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        audio.undo()
    assert take.read_bytes() == b"changed"
    take.write_bytes(original)
    audio.undo()
    receipt.undo()
    assert added.id not in engine.catalog(root=root)


def test_pack_sizes_use_the_same_units_for_cards_and_progress() -> None:
    from studio.pages.music_sounds_controls import pack_size

    assert [pack_size(size) for size in (0, 750, 1_250_000, 1_300_000_000)] == [
        "0.0 KB",
        "0.8 KB",
        "1.2 MB",
        "1.3 GB",
    ]

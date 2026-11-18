from pathlib import Path
from time import monotonic, sleep
from typing import IO, Any
from unittest.mock import patch

import miniaudio

from music.audio import FORMATS
from music_player import PreviewPlayer
from studio.pages.music_model import read_library

from .test_engine import wav


def test_new_music_visit_never_opens_unchanged_recordings(tmp_path: Path) -> None:
    game, library = tmp_path / "game", tmp_path / "profile/soundtracks"
    one = wav(game / "sound/BGM/deck/Stock.wav", 1)
    two = wav(library / "tf5/duel/Mine.wav", 2)
    open_file = Path.open
    reads = []

    def count(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if path.suffix.lower() in FORMATS:
            reads.append(path)
        return open_file(path, *args, **kwargs)

    with patch.object(Path, "open", count):
        first = read_library(game, library, lambda: None)
        assert reads.count(one) == reads.count(two) == 1
        reads.clear()
        second = read_library(game, library, lambda: None)
    assert first == second
    assert reads == []


def test_natural_eof_releases_device_before_next_command(tmp_path: Path) -> None:
    song = wav(tmp_path / "short.wav", 1)
    player = PreviewPlayer(backends=[miniaudio.Backend.NULL])
    try:
        player.play(song)
        limit = monotonic() + 3
        while player._device is not None and monotonic() < limit:
            sleep(0.015)
        assert player._device is None and player._stream is None and not player.playing
        assert player.path == song and player.position == player.duration > 0
        assert player.resume_position(song) == 0
        player.play(song)
        assert player.playing
    finally:
        player.stop()

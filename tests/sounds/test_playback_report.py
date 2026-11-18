import subprocess
import sys
from threading import Event

import miniaudio
import pytest

from music_player import PreviewPlayer
from sounds import AuditionPlayer, SoundSet, bundled_sets, set_sound_enabled, sound_settings
from sounds.audio import decode_clip


def test_preview_sampler_and_clean_stop_through_null_device(wav, monkeypatch):
    devices = []
    real_device = miniaudio.PlaybackDevice

    def track_device(*args, **kwargs):
        assert kwargs["backends"] == [miniaudio.Backend.NULL]
        device = real_device(*args, **kwargs)
        devices.append(device)
        return device

    monkeypatch.setattr(miniaudio, "PlaybackDevice", track_device)
    short = decode_clip(wav(), "wav")
    long = decode_clip(wav(4), "wav")
    sound_set = SoundSet("Preview", {"draw": short, "summon": short, "attack": long, "phase": None})
    player = AuditionPlayer(backends=[miniaudio.Backend.NULL])
    try:
        player.preview(sound_set, "draw").result(timeout=5)
        player.sampler(sound_set, events=("draw", "phase", "summon")).result(timeout=5)
        assert len(devices) == 3
        assert all(not device.running for device in devices)
        pending = player.preview(sound_set, "attack")
        replacement = player.preview(sound_set, "draw")
        pending.result(timeout=2)
        replacement.result(timeout=5)
        assert len(devices) in (4, 5)
        active = player.preview(sound_set, "attack")
        player.stop()
        active.result(timeout=2)
        assert all(not device.running for device in devices)
        assert not player.playing
        assert not player.preview(sound_set, "phase").result(timeout=2)
    finally:
        player.close()
    with pytest.raises(RuntimeError, match="closed"):
        player.preview(sound_set, "draw")


def test_native_startup_does_not_block_caller(wav, monkeypatch):
    started = Event()
    release = Event()
    open_device = PreviewPlayer.play_memory

    def slow_start(self, *args, **kwargs):
        started.set()
        assert release.wait(2)
        return open_device(self, *args, **kwargs)

    monkeypatch.setattr(PreviewPlayer, "play_memory", slow_start)
    player = AuditionPlayer(backends=[miniaudio.Backend.NULL])
    try:
        result = player.preview(SoundSet("Preview", {"draw": decode_clip(wav(), "wav")}), "draw")
        assert started.wait(2)
        assert player.playing and not result.done()
        player.stop()
        release.set()
        result.result(timeout=5)
        assert not player.playing
    finally:
        release.set()
        player.close()


def test_settings_preserve_bytes_and_check_whether_game_is_running(game, monkeypatch):
    config = game / "config/system.conf"
    config.parent.mkdir()
    before = (
        b"# note \xff\r\nenable_sound = 1\r\nenable_sound = 0 # effect switch\r\n"
        b"sound_volume = 37\r\nmusic_volume = 11\r\nsound_backend = SFML\r\n"
    )
    config.write_bytes(before)
    assert sound_settings(game).enabled is False
    assert sound_settings(game).volume == 37
    assert sound_settings(game).backend == "sfml"
    monkeypatch.setattr("music_library._game_running", lambda _: True)
    with pytest.raises(OSError, match="Close this EDOPro"):
        set_sound_enabled(game, True)
    assert config.read_bytes() == before
    monkeypatch.setattr("music_library._game_running", lambda _: False)
    set_sound_enabled(game, True)
    assert config.read_bytes() == before.replace(b"0 # effect switch", b"1 # effect switch")
    assert sound_settings(game).enabled is True


def test_report_is_read_only_and_matches_audio_by_content(game, tmp_path):
    thonk, synth = bundled_sets()
    draw = game / "sound/draw.wav"
    draw.write_bytes(thonk.assignments["draw"].data)
    summon = draw.with_name("summon.wav")
    summon.write_bytes(synth.assignments["summon"].data)
    library = tmp_path / "nonexistent-library"
    report = subprocess.run(
        [sys.executable, "-m", "sounds", "--report", str(game), "--library", str(library)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "enabled | Volume: 20%" in report
    assert "Card chants: 0" in report
    assert "draw\tsound/draw.wav\tWAV\t0.133 s\t23480\tThonk" in report
    assert next(line for line in report.splitlines() if line.startswith("summon\t")).endswith(
        "\tSynth"
    )
    assert "attack\t-\t-\t-\t-\t-" in report
    assert set((game / "sound").iterdir()) == {draw, summon}
    assert not library.exists()

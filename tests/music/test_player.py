import gc
import time
import wave

import miniaudio
import pytest

from edopal.features.music.player import PreviewError, PreviewPlayer


@pytest.fixture
def track(tmp_path):
    path = tmp_path / "星のテーマ.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 44_100, 0, "NONE", ""))
        wav.writeframes(b"\x00\x40" * 4410)
    return path


class ManualDevice:
    """Drive the real decoder one callback at a time without using a sound card."""

    backend = "Test"

    def __init__(self, **kwargs):
        self.running = self.closed = False
        self.callback = None

    def start(self, callback):
        self.callback = callback
        self.running = True

    def stop(self):
        self.callback = None
        self.running = False

    def close(self):
        self.stop()
        self.closed = True


@pytest.fixture
def manual(monkeypatch):
    monkeypatch.setattr(miniaudio, "PlaybackDevice", ManualDevice)
    return PreviewPlayer()


def test_stream_progress_pause_and_mute(manual, track):
    clip = manual.prepare(track)
    assert not clip.device.running
    assert not manual.playing
    manual.start(clip)
    assert manual.playing and manual.duration == pytest.approx(0.1)
    assert any(clip.device.callback.send(441))
    assert manual.position == pytest.approx(0.01)

    manual.pause()
    assert manual.paused and not manual.playing
    assert not clip.device.closed and not clip.device.running
    manual.set_muted(True)
    manual.resume()
    assert manual.playing and not manual.paused
    assert clip.device.callback.send(100) == bytes(100 * 2 * 2)
    manual.set_muted(False)
    assert any(clip.device.callback.send(100))
    with pytest.raises(StopIteration):
        while True:
            clip.device.callback.send(1024)
    assert not manual.playing and clip.device.closed and manual.error is None
    assert manual.position == pytest.approx(0.1)
    manual.stop()
    assert manual.position == manual.duration == 0


def test_replacing_hiding_and_discarding_previews(manual, track):
    first = manual.prepare(track)
    manual.start(first)
    obsolete = manual.prepare(track)
    unused_device = obsolete.device
    del obsolete
    gc.collect()
    assert unused_device.closed and manual.playing
    second = manual.prepare(track)
    manual.start(second)
    assert first.device.closed and manual.playing
    manual.stop()
    manual.stop()
    assert second.closed and not manual.playing
    with pytest.raises(PreviewError, match="closed"):
        manual.start(second)


def test_invalid_or_missing_audio_is_recoverable(manual, track):
    manual.play(track)
    invalid = track.with_name("broken.wav")
    invalid.write_bytes(b"not a recording")
    with pytest.raises(PreviewError, match="Could not read"):
        manual.play(invalid)
    assert not manual.playing and not manual.paused
    with pytest.raises(PreviewError, match="Could not read"):
        manual.play(track.with_suffix(".missing"))


def test_output_failure_closes_prepared_stream(manual, track, monkeypatch):
    clip = manual.prepare(track)

    def failed_start(callback):
        raise miniaudio.MiniaudioError("Output failed")

    monkeypatch.setattr(clip.device, "start", failed_start)
    with pytest.raises(PreviewError, match="audio output"):
        manual.start(clip)
    assert clip.closed and not manual.playing


def test_extensionless_cache_entry_plays_with_real_null_device(track):
    clip = track.rename(track.with_suffix(""))
    player = PreviewPlayer(backends=[miniaudio.Backend.NULL])
    try:
        player.play(clip)
        deadline = time.monotonic() + 3
        while player.playing and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not player.playing and player.error is None
        assert player.position == pytest.approx(0.1)
    finally:
        player.stop()

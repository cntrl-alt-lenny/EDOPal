"""Stream one local preview at a time through miniaudio, independently of Qt."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import miniaudio

_RATE = 44_100
_CHANNELS = 2


class PreviewError(RuntimeError):
    """The audio file or playback device could not be opened."""


@dataclass
class _Progress:
    frames: int = 0
    finished: bool = False
    muted: bool = False
    error: str | None = None


def _output(decoder, progress: _Progress):
    # miniaudio sends the frame count needed by the device on each callback.
    requested = yield b""
    try:
        while True:
            samples = decoder.send(requested)
            progress.frames += len(samples) // _CHANNELS
            requested = yield bytes(len(samples) * samples.itemsize) if progress.muted else samples
    except StopIteration:
        pass
    except (miniaudio.MiniaudioError, ValueError, OSError):
        progress.error = "The audio file could not be decoded to the end."
    finally:
        progress.finished = True


@dataclass
class PreparedPreview:
    """A silent, open stream. Pass it to start(), or close() if selection changed."""

    duration: float
    device: miniaudio.PlaybackDevice
    callback: Generator
    progress: _Progress
    _resources: ExitStack
    closed: bool = False

    def close(self) -> None:
        self._resources.close()
        self.closed = True

    def __del__(self):
        # A cancelled job may discard its result without delivering on_done.
        self.close()


class PreviewPlayer:
    """Prepare on a worker. Control playback and poll its state from the owning thread."""

    def __init__(self, *, backends: Sequence[miniaudio.Backend] | None = None):
        self._backends = list(backends) if backends is not None else None
        self._current: PreparedPreview | None = None
        self._paused = False
        self._muted = False

    def prepare(self, path: Path) -> PreparedPreview:
        """Open the decoder and device without altering or starting playback."""
        path = Path(path)
        with ExitStack() as resources:
            try:
                decoder = miniaudio.stream_file(str(path), nchannels=_CHANNELS, sample_rate=_RATE)
                resources.callback(decoder.close)
            except (OSError, ValueError, miniaudio.MiniaudioError) as exc:
                raise PreviewError(f"Could not read the audio in {path.name}.") from exc

            # Cached clips may lack a filename extension. The decoder sniffs the
            # format, while miniaudio's optional duration lookup needs the suffix.
            try:
                duration = miniaudio.get_file_info(str(path)).duration
            except (OSError, ValueError, miniaudio.MiniaudioError):
                duration = 0.0

            progress = _Progress()
            callback = _output(decoder, progress)
            next(callback)
            resources.callback(callback.close)
            try:
                device = miniaudio.PlaybackDevice(
                    nchannels=_CHANNELS, sample_rate=_RATE, buffersize_msec=60, backends=self._backends
                )
                resources.callback(device.close)
                if device.backend.lower() == "null" and miniaudio.Backend.NULL not in (self._backends or ()):
                    raise PreviewError("Connect an audio output to play this preview.")
            except (OSError, miniaudio.MiniaudioError) as exc:
                raise PreviewError("Could not open the audio output for this preview.") from exc
            return PreparedPreview(duration, device, callback, progress, resources.pop_all())

    def start(self, prepared: PreparedPreview) -> None:
        self.stop()
        if prepared.closed:
            raise PreviewError("This preview has already been closed.")
        self._current = prepared
        prepared.progress.muted = self._muted
        self._start_device(prepared)

    def play(self, path: Path) -> None:
        self.stop()
        self.start(self.prepare(path))

    def _start_device(self, prepared: PreparedPreview) -> None:
        try:
            prepared.device.start(prepared.callback)
        except (OSError, miniaudio.MiniaudioError) as exc:
            self.stop()
            raise PreviewError("Could not start the preview on this audio output.") from exc
        self._paused = False

    def pause(self) -> None:
        if self.playing:
            try:
                self._current.device.stop()
            except (OSError, miniaudio.MiniaudioError) as exc:
                self.stop()
                raise PreviewError("The audio output disconnected.") from exc
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._start_device(self._current)

    def stop(self) -> None:
        if self._current:
            self._current.close()
            self._current = None
        self._paused = False

    @property
    def playing(self) -> bool:
        preview = self._current
        if preview is None or preview.closed or self._paused:
            return False
        if preview.progress.finished or not preview.device.running:
            # Device teardown must happen outside its own audio callback.
            if not preview.progress.finished:
                preview.progress.error = "The audio output disconnected."
            preview.close()
            return False
        return True

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def duration(self) -> float:
        return self._current.duration if self._current else 0.0

    @property
    def position(self) -> float:
        return self._current.progress.frames / _RATE if self._current else 0.0

    @property
    def error(self) -> str | None:
        return self._current.progress.error if self._current else None

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if self._current:
            self._current.progress.muted = muted


def audio_profile(path: Path) -> tuple[float, tuple[float, ...]]:
    """Read a low-rate mono overview for the preview's waveform on its worker."""
    try:
        samples = miniaudio.decode_file(str(path), nchannels=1, sample_rate=2000).samples
    except (OSError, ValueError, miniaudio.MiniaudioError):
        return 0.0, ()
    step = max(1, (len(samples) + 191) // 192)
    levels = []
    for start in range(0, len(samples), step):
        section = samples[start : start + step : 4]
        levels.append((sum(v * v for v in section) / len(section)) ** 0.5)
    peak = max(levels, default=0) or 1
    return len(samples) / 2000, tuple((v / peak) ** 0.84 for v in levels)

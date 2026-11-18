"""A small streaming player shared by music and duel-effect previews."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miniaudio import Backend


class PreviewPlayer:
    RATE = 44100

    def __init__(self, *, backends: list[Backend] | None = None) -> None:
        self.path: Path | None = None
        self.duration = 0.0
        self._offset = 0.0
        self._started = 0.0
        self._running = False
        self._device = None
        self._stream = None
        self._process = None
        self._native = True
        self._backends = backends

    @property
    def playing(self) -> bool:
        process = self._process
        if process is not None and process.poll() is not None:
            if self._running:
                self._offset = time.monotonic() - self._started
            self._running = False
        return self._running

    @property
    def position(self) -> float:
        elapsed = time.monotonic() - self._started if self.playing else 0
        position = self._offset + elapsed
        return min(self.duration, position) if self.duration else position

    @property
    def seekable(self) -> bool:
        return self._native and self.duration > 0

    def _release(self):
        if self._device is not None:
            try:
                self._device.close()
            finally:
                self._device = None
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._process is not None:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                self._process.terminate()
                self._process.wait(timeout=0.5)
            if self._process.poll() is None:
                self._process.kill()
                self._process.wait()
            self._process = None
        self._running = False

    def resume_position(self, path: Path) -> float:
        if self.path != path.absolute():
            return 0.0
        position = self.position
        return 0.0 if self.duration and position >= self.duration - 0.1 else position

    def stop(self) -> None:
        self._release()
        self.path = None
        self._offset = self.duration = 0.0

    def pause(self):
        self._offset = self.position
        self._release()

    def seek(self, seconds: float):
        if self.seekable and self.path:
            offset = max(0.0, min(seconds, self.duration - 0.05))
            if self.playing:
                self.play(self.path, seconds=offset)
            else:
                self._offset = offset

    def _ended(self):
        self._offset = self.duration
        self._running = False

    def play(self, path: Path, *, seconds: float = 0):
        self.stop()
        path = Path(path).resolve(strict=True)
        self.path = path
        try:
            import miniaudio
        except ImportError:
            self._native = False
            try:
                self._play_system(path)
            except OSError:
                self.stop()
                raise
        else:
            self._native = True
            try:
                self.duration = miniaudio.get_file_info(str(path)).duration
                self._offset = max(0.0, min(seconds, max(0.0, self.duration - 0.05)))
                self._stream = miniaudio.stream_file(
                    str(path), sample_rate=self.RATE, seek_frame=round(self._offset * self.RATE)
                )
                self._start_native()
            except (miniaudio.MiniaudioError, OSError, ValueError) as exc:
                self.stop()
                raise OSError(
                    f"Could not play {path.name}. Check your audio output and file format. {exc}"
                ) from exc

    def play_memory(self, data: bytes, *, duration: float) -> None:
        """Stream a validated effect without creating a temporary audio file."""
        import miniaudio

        self.stop()
        self._native = True
        self.duration = duration
        try:
            self._stream = miniaudio.stream_memory(data, sample_rate=self.RATE)
            self._start_native()
        except (miniaudio.MiniaudioError, OSError, ValueError) as exc:
            self.stop()
            raise OSError(f"Could not play this cue. Check your audio output. {exc}") from exc

    def _start_native(self) -> None:
        import miniaudio

        callback = miniaudio.stream_with_callbacks(self._stream, end_callback=self._ended)
        next(callback)
        self._device = miniaudio.PlaybackDevice(
            sample_rate=self.RATE, buffersize_msec=80, backends=self._backends
        )
        self._running = True
        self._started = time.monotonic()
        self._device.start(callback)

    def _play_system(self, path: Path):
        env = os.environ.copy()
        if sys.platform == "win32":
            # WPF is included with Windows PowerShell and uses the installed system codecs.
            env["EDOPRO_PREVIEW_AUDIO"] = str(path)
            code = (
                "Add-Type -AssemblyName PresentationCore; "
                "$p=[System.Windows.Media.MediaPlayer]::new(); "
                "$p.Open([uri]$env:EDOPRO_PREVIEW_AUDIO); $p.Play(); "
                "$limit=(Get-Date).AddSeconds(10); "
                "while (!$p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $limit) "
                "{Start-Sleep -Milliseconds 50}; "
                "if (!$p.NaturalDuration.HasTimeSpan) {exit 1}; "
                "Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds)"
            )
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", code]
        elif sys.platform == "darwin" and shutil.which("afplay"):
            command = ["afplay", str(path)]
        elif backend := shutil.which("ffplay"):
            command = [backend, "-nodisp", "-nostats", "-loglevel", "error", "-autoexit", str(path)]
        elif backend := shutil.which("gst-play-1.0"):
            command = [backend, "--no-interactive", path.as_uri()]
        else:
            raise OSError("Install the app’s miniaudio dependency to enable previews.")
        self._process = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._running = True
        self._started = time.monotonic()

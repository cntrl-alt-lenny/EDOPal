"""Serialize device access and discard superseded preview requests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Lock

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QSlider

from music.view import SeekState
from music_player import PreviewPlayer
from studio import theme as t
from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import Panel, label

from .music_widgets import action, row


class SeekBar(QSlider):
    def __init__(self, preview: Preview) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.preview = preview
        self.setRange(0, 1000)
        self.setAccessibleName("Preview position")
        self.sliderMoved.connect(lambda n: self.preview.scrub(n / self.maximum()))
        self.sliderReleased.connect(self.preview.release)
        self.actionTriggered.connect(self._clicked)

    def _clicked(self, _action: int) -> None:
        if not self.isSliderDown():
            self.preview.scrub(self.sliderPosition() / self.maximum())
            self.preview.release()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = Qt.Key(event.key()).name.removeprefix("Key_")
        if key in ("Home", "End", "Right", "Left", "Up", "Down"):
            p = self.preview
            p.seek.keyboard(key, p.player.position, p.player.duration)
            p.release()
            event.accept()
        else:
            super().keyPressEvent(event)


def clock(seconds: float) -> str:
    minutes, remainder = divmod(max(int(seconds), 0), 60)
    return f"{minutes}:{remainder:02}"


class Preview(Panel):
    def __init__(self, tasks: TaskRunner, error: Callable[[str], None]) -> None:
        super().__init__(t.LG)
        self.tasks, self.error = tasks, error
        self.player = PreviewPlayer()
        self.lock = Lock()
        self.seek = SeekState()
        self.generation = 0
        self.pending = False
        self.path: Path | None = None
        self.play = action("Play preview", self.toggle, icon="music")
        self.slider = SeekBar(self)
        self.time = label("0:00 / 0:00", "small")
        self.body.addLayout(row(self.play, self.slider, self.time))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_clock)
        self.timer.setInterval(t.MOTION_MS)
        self.update_clock()

    def command(self, work: Callable[[], None]) -> None:
        self.generation += 1
        current = self.generation
        self.pending = True
        self.play.setEnabled(False)
        self.slider.setEnabled(False)

        def run(worker: WorkerContext) -> None:
            with self.lock:
                if current == self.generation:
                    work()

        job = self.tasks.start(run)
        job.error.connect(lambda exc: self.error(str(exc)) if current == self.generation else None)

        def finished() -> None:
            if current == self.generation:
                self.pending = False
                self.update_clock()

        job.finished.connect(finished)

    def stop(self) -> None:
        self.path = None
        self.seek.fraction = None
        self.timer.stop()
        if self.pending or self.player.path:
            self.command(self.player.stop)
        else:
            self.update_clock()

    def select(self, path: Path | None) -> None:
        if self.path != path:
            self.stop()
            self.path = path
        self.update_clock()
        if path:
            self.timer.start()

    def toggle(self) -> None:
        if not self.path or self.pending:
            return
        path = self.path
        if self.player.playing and self.player.path == path:
            self.command(self.player.pause)
        else:
            self.command(lambda: self.player.play(path, seconds=self.player.resume_position(path)))

    def scrub(self, fraction: float) -> None:
        self.seek.drag(fraction)
        self.update_clock()

    def release(self) -> None:
        seconds = self.seek.release(self.player.duration)
        if seconds is not None and not self.pending:
            self.command(lambda: self.player.seek(seconds))

    def update_clock(self) -> None:
        if self.pending:
            return
        active = self.path is not None and self.player.path == self.path
        duration = self.player.duration if active else 0
        position = self.seek.position(self.player.position, duration) if active else 0
        self.time.setText(f"{clock(position)} / {clock(duration)}")
        self.play.setText("Pause" if active and self.player.playing else "Play preview")
        self.play.setEnabled(self.path is not None)
        if not self.slider.isSliderDown():
            self.slider.setValue(round(position * 1000 / duration) if duration else 0)
        self.slider.setEnabled(active and self.player.seekable)

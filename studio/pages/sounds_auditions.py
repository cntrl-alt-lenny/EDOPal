"""Audition controls share one player and one active preview."""

from concurrent.futures import Future
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer

import sounds
from studio import theme as t
from studio.widgets import Button

if TYPE_CHECKING:
    from studio.pages.sounds import SoundsPage


class SoundAuditions:
    def __init__(self, page: "SoundsPage") -> None:
        self.page = page
        self.player = sounds.AuditionPlayer()
        page.destroyed.connect(lambda *_, player=self.player: player.close())
        self.preview: Future[None] | None = None
        self.button: Button | None = None
        self.original_text = ""
        self.timer = QTimer(page)
        self.timer.setInterval(t.MOTION_MS)
        self.timer.timeout.connect(self.check)

    def play(self, button: Button, sound_set: sounds.SoundSet, key: str = "") -> None:
        if self.button is button:
            self.stop()
            return
        self.stop()
        try:
            self.preview = (
                self.player.preview(sound_set, key) if key else self.player.sampler(sound_set)
            )
        except (OSError, RuntimeError, ValueError):
            self.page.feedback.show_message(
                "Could not play that sound. Check your audio output and try again."
            )
            return
        self.button, self.original_text = button, button.text()
        button.setText("Stop sampler" if not key else "Stop")
        self.timer.start()

    def reset(self) -> None:
        self.timer.stop()
        if self.button is not None:
            self.button.setText(self.original_text)
        self.button = self.preview = None

    def stop(self) -> None:
        if self.preview is not None:
            self.player.stop()
        self.reset()

    def check(self) -> None:
        if self.preview is None or not self.preview.done():
            return
        failed = not self.preview.cancelled() and self.preview.exception() is not None
        self.reset()
        if failed:
            self.page.feedback.show_message(
                "Could not play that sound. Check your audio output and try again."
            )

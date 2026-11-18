"""Audition controls share one player and one active preview."""

from concurrent.futures import Future
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from shiboken6 import isValid

import sounds
from studio import theme as t
from studio.pages.music_sounds_controls import no_device
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
        self.original_hint = ""
        self.original_description = ""
        self.timer = QTimer(page)
        self.timer.setInterval(t.MOTION_MS)
        self.timer.timeout.connect(self.check)

    def play(self, button: Button, sound_set: sounds.SoundSet, key: str = "") -> None:
        if self.button is button and self.preview is not None:
            self.stop()
            return
        self.stop()
        self.button, self.original_text = button, button.text()
        self.original_hint = button.toolTip()
        self.original_description = button.accessibleDescription()
        try:
            self.preview = (
                self.player.preview(sound_set, key) if key else self.player.sampler(sound_set)
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed(exc)
            return
        button.setText("Stop preview")
        self.timer.start()

    def reset(self) -> None:
        self.timer.stop()
        if self.button is not None and isValid(self.button):
            self.button.setText(self.original_text)
            self.button.setToolTip(self.original_hint)
            self.button.setAccessibleDescription(self.original_description)
        self.button = self.preview = None

    def stop(self) -> None:
        if self.preview is not None:
            self.player.stop()
        self.reset()

    def check(self) -> None:
        if self.preview is None or not self.preview.done():
            return
        error = None if self.preview.cancelled() else self.preview.exception()
        if error is None:
            self.reset()
        else:
            self.failed(error)

    def failed(self, error: BaseException) -> None:
        self.timer.stop()
        self.preview = None
        if no_device(error) and self.button is not None and isValid(self.button):
            self.button.setText("No audio device")
            hint = "No audio device. Connect an audio output and press to preview again."
            self.button.setToolTip(hint)
            self.button.setAccessibleDescription(hint)
        else:
            self.reset()
            self.page.feedback.show_message(
                "Could not play that sound. Check your audio output and try again."
            )

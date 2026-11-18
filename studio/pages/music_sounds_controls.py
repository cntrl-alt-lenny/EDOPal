"""Page-local controls shared by the two audio editors."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QWidget

import config
from content.cache import PackCache
from content.catalogue import PackState, PackStatus
from music.settings import GameProcessUnknown
from studio.context import AppContext, GameOpenError
from studio.widgets import Button, GameClosedBlock


def check_game(game: Path, confirmed_closed: bool) -> None:
    try:
        AppContext.require_game_closed(game)
    except GameOpenError as exc:
        if not isinstance(exc.__cause__, OSError):
            raise
        if not confirmed_closed:
            raise GameProcessUnknown(str(exc)) from exc


# Both editors use the same landing breakpoints while the shell hero is being updated.
BANNER_HEIGHT = 152
TITLE_ROW_HEIGHT = 650


class AudioGameCheck(GameClosedBlock):
    def __init__(self) -> None:
        super().__init__()
        self.heading = self.body.itemAt(0).widget()
        self.body.removeWidget(self.retry)
        self.back = Button("Back", "quiet")
        self.proceed = Button("EDOPro is closed — continue", "secondary")
        self.actions = actions = QHBoxLayout()
        actions.addWidget(self.back)
        actions.addStretch()
        actions.addWidget(self.proceed)
        actions.addWidget(self.retry)
        actions.addStretch()
        self.body.addLayout(actions)

    def explain(self, exc: BaseException) -> None:
        unknown = isinstance(exc, GameProcessUnknown)
        self.heading.setText("Is EDOPro closed?" if unknown else "Close EDOPro to continue")
        self.retry.setText("Check again" if unknown else "Retry")
        self.back.setVisible(unknown)
        self.actions.setStretch(1, int(unknown))
        self.actions.setStretch(4, int(not unknown))
        self.proceed.setVisible(unknown)
        self.show_reason(
            f"{config.APP_NAME} could not tell whether EDOPro is open. "
            "Check again, or close EDOPro and confirm to continue."
            if unknown
            else str(exc)
        )


def confirm_change(parent: QWidget, action: str, message: str) -> bool:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(action)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(message)
    cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
    accept = dialog.addButton(action, QMessageBox.ButtonRole.AcceptRole)
    dialog.setDefaultButton(cancel)
    dialog.setEscapeButton(cancel)
    dialog.exec()
    return dialog.clickedButton() is accept


def no_device(exc: BaseException) -> bool:
    # Native playback wraps the device error with the track or cue name.
    return isinstance(exc, OSError) and "no audio device" in str(exc).casefold()


def pack_size(size: float) -> str:
    for unit in ("KB", "MB", "GB"):
        size /= 1000
        if size < 1000 or unit == "GB":
            return f"{size:,.1f} {unit}"
    raise AssertionError


def partial_archives(cache: PackCache) -> set[tuple[str, str]]:
    return {
        (entry.pack_id, entry.path.name)
        for entry in cache.list_cached()
        if entry.category == "partial" and entry.size_bytes
    }


def can_resume(status: PackStatus, partials: set[tuple[str, str]]) -> bool:
    pack = status.pack
    return (
        status.state in (PackState.NOT_DOWNLOADED, PackState.UPDATE_AVAILABLE)
        and (pack.id, f"{pack.version}--{pack.sha256}.zip.part") in partials
    )

"""Named confirmations for changes to the appearance library or game."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

import config
from studio.pages.base import Page
from studio.widgets import Button


class AppearancePage(Page):
    def game_warning(self, exc: Exception) -> str:
        if exc.__cause__:
            return f"{config.APP_NAME} couldn’t check whether EDOPro is open. Close it, then choose Retry."
        return "Close EDOPro to continue. Close this copy of EDOPro, then choose Retry."

    def setup_feedback(self) -> None:
        self.details = Button("Technical details", "quiet")
        self.details.hide()
        self.details.clicked.connect(self.show_details)

    def show_details(self) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Technical details")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(self.details.toolTip())
        dialog.exec()

    def technical_error(self, exc: Exception | None) -> None:
        self.details.setVisible(exc is not None)
        self.details.setToolTip(str(exc) if exc else "")

    def confirm(self, title: str, message: str) -> bool:
        discard = title == "Discard unsaved changes?"
        dialog = QMessageBox(QMessageBox.Icon.Question, title, message, parent=self)
        cancel = dialog.addButton(
            "Keep editing" if discard else "Cancel", QMessageBox.ButtonRole.RejectRole
        )
        action = dialog.addButton(
            "Discard changes" if discard else title.rstrip("?"), QMessageBox.ButtonRole.AcceptRole
        )
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() == action

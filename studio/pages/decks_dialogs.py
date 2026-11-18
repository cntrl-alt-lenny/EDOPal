"""Confirmations shared by the tournament and personal deck pages."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

GAME_OPEN_DETAIL = "EDOPro is open in the selected game folder."
GAME_CHECK_DETAIL = "Couldn’t check whether EDOPro is open in the selected game folder."


def confirm_deck_change(parent: QWidget, action: str, message: str) -> bool:
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

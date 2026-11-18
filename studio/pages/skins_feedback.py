"""Named confirmations for changes to the appearance library or game."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from studio.pages.base import Page
from studio.widgets import Button, GameClosedBlock, margins


class AppearancePage(Page):
    def setup_block(self) -> None:
        self.block_screen = QWidget()
        column = QVBoxLayout(self.block_screen)
        margins(column, 0)
        back = Button("Back", "quiet", "back")
        back.clicked.connect(self.leave_block)
        column.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        self.block = GameClosedBlock()
        self.block.retry.clicked.connect(self.retry_block)
        column.addWidget(self.block)
        column.addStretch()
        self.stack.addWidget(self.block_screen)
        self.block_action: Callable | None = None
        self.block_return: QWidget | None = None
        self.block_hidden: list[QWidget] = []

    def show_block(self, error: Exception, retry: Callable, *chrome: QWidget) -> None:
        self.block_return = self.stack.currentWidget()
        self.block_hidden = [w for w in chrome if not w.isHidden()]
        for widget in self.block_hidden:
            widget.hide()
        self.block_action = retry
        self.stack.setCurrentWidget(self.block_screen)
        self.block.show_reason(str(error))

    def leave_block(self) -> bool:
        if self.block_return is None:
            return False
        self.stack.setCurrentWidget(self.block_return)
        for widget in self.block_hidden:
            widget.show()
        self.block_return = self.block_action = None
        self.block_hidden.clear()
        return True

    def retry_block(self) -> None:
        action = self.block_action
        if self.leave_block() and action:
            action()

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
        cancel = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        action = dialog.addButton(
            "Discard changes" if discard else title.rstrip("?"), QMessageBox.ButtonRole.AcceptRole
        )
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() == action

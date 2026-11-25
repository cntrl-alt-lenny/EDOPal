"""Shared page title and margins for the shell's Settings and library pages."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from edopal.ui import theme as t


def label(text, role="", *, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def rule():
    line = QFrame()
    line.setObjectName("quietRule")
    line.setFixedHeight(t.HAIR)
    line.setStyleSheet(f"background: {t.RULE};")
    return line


class ShellPage(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setSpacing(17)
        self.title_row = QHBoxLayout()
        heading = label(title, "pageTitle")
        heading.setFixedHeight(56)
        self.title_row.addWidget(heading, 1)
        self.body.addLayout(self.title_row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.window().width() > t.WIDE_WIDTH
        side = 36 if wide else t.CONTENT_MARGIN
        self.body.setContentsMargins(side, 19 if wide else 15, side, 33 if wide else t.XL)
        self.body.setSpacing(20 if wide else 17)

"""Sounds navigation while the effects library is in development."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QVBoxLayout

from edopal.core.feature import FeatureSpec
from edopal.ui import theme as t
from edopal.ui.page_chrome import ShellPage, label


class SoundsPage(ShellPage):
    def __init__(self, _core):
        super().__init__("Sounds")
        self.body.addStretch()
        content = QVBoxLayout()
        content.setSpacing(t.SM)
        mark = label("")
        mark.setPixmap(t.icon("sounds", t.GOLD).pixmap(QSize(38, 38)))
        content.addWidget(mark, alignment=Qt.AlignmentFlag.AlignHCenter)
        content.addWidget(label("Coming Soon", "heading"), alignment=Qt.AlignmentFlag.AlignHCenter)
        content.addWidget(
            label("Preview and choose sound effects for EDOPro.", "body"), alignment=Qt.AlignmentFlag.AlignHCenter
        )
        self.body.addLayout(content)
        self.body.addStretch()


def sounds_spec():
    return FeatureSpec("sounds", "Sounds", "sounds", 35, SoundsPage, "EDOPro sound effects")

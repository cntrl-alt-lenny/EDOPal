"""The sidebar release link and its three visual states."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QHBoxLayout

from edopal.core.updates import RELEASES, UpdateCheck
from edopal.ui import theme as t
from edopal.ui.page_chrome import label
from edopal.ui.widgets import QuietButton


class ReleaseButton(QuietButton):
    def __init__(self, dpr):
        super().__init__("")
        self.setObjectName("releaseStatus")
        self.setFixedHeight(26)
        self.dpr = dpr
        self.result = None
        self.url = RELEASES
        self.compact = False
        self.hovered = False
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(10, 0, 10, 0)
        self.row.setSpacing(5)
        self.mark = label("")
        self.title = label("", "releaseTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.arrow = label("")
        for child in (self.mark, self.title, self.arrow):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.row.addWidget(child)
        self.arrow.setPixmap(t.icon("external", t.GOLD).pixmap(QSize(14, 14), dpr))
        self.set_result(None)

    def set_result(self, result: UpdateCheck | None):
        self.result = result
        available = bool(result and result.available)
        self.url = result.url if available else RELEASES
        if available:
            title = f"Update to v{result.latest.removeprefix('v')}"
            tip = "Open the EDOPal release on GitHub"
            if result.failed:
                tip = "Open the last verified EDOPal release on GitHub"
        elif not result:
            title, tip = "Checking…", "Checking GitHub for the latest release"
        elif result.failed:
            title, tip = "Check Unavailable", "Couldn’t check for updates. Open releases on GitHub."
        else:
            title, tip = "Up to Date", "Open EDOPal releases on GitHub"
        self.title.setText(title)
        self.setAccessibleName(title)
        self.setToolTip(tip)
        self.setProperty("updateAvailable", available)
        self.style().unpolish(self)
        self.style().polish(self)
        self._update_contents()

    def set_compact(self, compact):
        self.compact = compact
        self._update_contents()

    def enterEvent(self, event: QEnterEvent):
        self.hovered = True
        self._update_contents()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self._update_contents()
        super().leaveEvent(event)

    def _update_contents(self):
        available = bool(self.property("updateAvailable"))
        verified = bool(self.result and not self.result.failed and not available)
        text_colour = t.ON_GOLD if available else t.INK if self.hovered else t.TEXT
        self.title.setStyleSheet(f"color: {text_colour}; font-weight: {700 if available else 500};")
        glyph, colour = ("check", t.GREEN) if verified else ("external", t.GOLD)
        if self.compact and available:
            colour = t.ON_GOLD
        self.mark.setPixmap(t.icon(glyph, colour).pixmap(QSize(16, 16), self.dpr))
        self.mark.setVisible(self.compact or verified)
        self.title.setVisible(not self.compact)
        self.arrow.setVisible(self.hovered and verified and not self.compact)
        if self.compact:
            width = 34
        else:
            width = self.title.fontMetrics().horizontalAdvance(self.title.text()) + 20
            width += 21 if verified else 0
            width += 19 if self.hovered and verified else 0
            if available:
                width = min(154, max(134, width))
                title = self.title.fontMetrics().elidedText(self.accessibleName(), Qt.TextElideMode.ElideRight, width - 20)
                self.title.setText(title)
        self.setFixedWidth(width)

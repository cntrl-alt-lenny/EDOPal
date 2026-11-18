"""Keep deck lists in the viewport when their own item views handle scrolling."""

from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QScrollArea, QWidget


class DeckPageScroll(QScrollArea):
    def setWidget(self, widget: QWidget) -> None:
        super().setWidget(widget)
        self._fit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if not self.widgetResizable() and (page := self.widget()) is not None:
            width = min(page.maximumWidth(), self.viewport().width())
            page.resize(width, self.viewport().height())

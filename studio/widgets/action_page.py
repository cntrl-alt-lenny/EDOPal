"""Keep the next action visible and place surplus height below the task."""

from PySide6.QtCore import QEvent
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from studio import theme as t
from studio.widgets.controls import margins


class ActionPage(QWidget):
    def __init__(self, content: QScrollArea | QStackedWidget, actions: QWidget) -> None:
        super().__init__()
        self.content = content
        self.actions = actions
        actions.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.maximum = content.maximumHeight()
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.setSpacing(0)
        layout.addWidget(content, 1)
        layout.addWidget(actions)
        layout.addStretch()
        if isinstance(content, QStackedWidget):
            content.currentChanged.connect(self._fit)
            for i in range(content.count()):
                content.widget(i).widget().installEventFilter(self)
        else:
            content.widget().installEventFilter(self)

    def _fit(self) -> None:
        area = self.content
        if isinstance(area, QStackedWidget):
            area = area.currentWidget()
        height = self.maximum
        if area and self.window().height() > t.COMPACT_HERO_HEIGHT:
            child = area.widget()
            layout = child.layout()
            width = min(area.viewport().width(), child.maximumWidth())
            height = max(
                layout.minimumSize().height(),
                layout.totalHeightForWidth(width),
                layout.sizeHint().height() if not layout.hasHeightForWidth() else 0,
            )
            height += 2 * area.frameWidth()
        if self.content.maximumHeight() != height:
            self.content.setMaximumHeight(height)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutRequest and watched.isVisible():
            self._fit()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        inset = max(t.LG, (self.width() - t.WIDE_CONTENT) // 2 + t.XXL)
        self.actions.setContentsMargins(inset, t.SM, inset, t.MD)
        self._fit()

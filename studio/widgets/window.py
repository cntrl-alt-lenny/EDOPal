"""Persist portable window state without asking Wayland to place a surface."""

from PySide6.QtCore import QRect, QSettings, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QSizePolicy, QWidget

from studio import geometry
from studio import theme as t


def restore_window(window: QWidget, settings: QSettings) -> None:
    if not QApplication.platformName().startswith("wayland"):
        geometry.restore(window, settings)
        return
    # Read the size from an older profile without using its position to pick a monitor.
    size = settings.value("window/size")
    legacy = settings.value("window/normal")
    if not isinstance(size, QSize) or size.isEmpty():
        size = (
            legacy.size()
            if isinstance(legacy, QRect) and legacy.isValid()
            else QSize(*t.DEFAULT_SIZE)
        )
    window.winId()
    frame = window.windowHandle().frameMargins()
    decor = QSize(frame.left() + frame.right(), frame.top() + frame.bottom())
    available = (window.screen().availableGeometry().size() - decor).expandedTo(QSize(1, 1))
    minimum = QSize(*t.MIN_SIZE).boundedTo(available)
    window.setMinimumSize(minimum)
    window.resize(size.expandedTo(minimum).boundedTo(available))
    if settings.value("window/maximized", False, type=bool):
        window.showMaximized()


def save_window(window: QWidget, settings: QSettings) -> None:
    if not QApplication.platformName().startswith("wayland"):
        settings.remove("window/size")
        geometry.save(window, settings)
        return
    size = window.normalGeometry().size() if window.isMaximized() else window.size()
    settings.setValue("window/size", size)
    settings.setValue("window/maximized", window.isMaximized())
    settings.sync()


class WorkspaceScroll(QScrollArea):
    """Only scroll the entire workspace when the display cannot fit a regular page."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWidget(QWidget())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not self.widgetResizable():
            self.widget().resize(self.viewport().size())

    def fit(self, size: QSize, *, page_visible: bool) -> None:
        small = size.width() < t.MIN_SIZE[0] or size.height() < t.MIN_SIZE[1]
        page_width = t.MIN_SIZE[0] - t.RAIL_COMPACT
        small_page = size.width() < page_width and page_visible
        self.setWidgetResizable(small)
        bars = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if small else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setHorizontalScrollBarPolicy(bars)
        self.setVerticalScrollBarPolicy(bars)
        # Below the supported page layout size, scroll the complete surface, including
        # its header and the picker. Nested pages retain their own focus scrolling.
        policy = QSizePolicy.Policy.Preferred if small_page else QSizePolicy.Policy.Ignored
        self.widget().setSizePolicy(policy, policy)
        self.widget().setMinimumSize(
            page_width if small_page else 0,
            t.MIN_SIZE[1] if size.height() < t.MIN_SIZE[1] else 0,
        )

        if not small:
            # Supported window sizes use the page's own scroll surface. A nested
            # height-for-width hint must not grow a second, outer scrollbar.
            self.widget().resize(self.viewport().size())

    def reveal_focus(self, _old: QWidget | None, focus: QWidget | None) -> None:
        if focus and self.widget().isAncestorOf(focus):
            self.ensureWidgetVisible(focus, t.SM, t.SM)

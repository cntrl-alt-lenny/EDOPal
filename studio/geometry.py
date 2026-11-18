"""Window placement in logical pixels, including monitor removal and smaller displays."""

from PySide6.QtCore import QPoint, QRect, QSettings, QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from studio import theme as t


def fit_geometry(saved: QRect | None, available: QRect, frame: QSize | None = None) -> QRect:
    frame = frame or QSize(0, 0)
    width = max(1, available.width() - frame.width())
    height = max(1, available.height() - frame.height())
    size = saved.size() if saved and saved.isValid() else QSize(*t.DEFAULT_SIZE)
    size = size.expandedTo(QSize(*t.MIN_SIZE)).boundedTo(QSize(width, height))
    bounds = QRect(available.topLeft(), QSize(width, height))
    rect = QRect(QPoint(), size)
    rect.moveCenter(bounds.center())
    if saved and saved.intersects(available):
        rect.moveLeft(max(bounds.left(), min(saved.x(), bounds.right() - rect.width() + 1)))
        rect.moveTop(max(bounds.top(), min(saved.y(), bounds.bottom() - rect.height() + 1)))
    return rect


def restore(window: QWidget, settings: QSettings) -> None:
    saved = settings.value("window/normal")
    if not isinstance(saved, QRect) or not saved.isValid():
        saved = None
    screen = QGuiApplication.screenAt(saved.center()) if saved else None
    screen = screen or window.screen()
    available = screen.availableGeometry()
    window.winId()
    frame = window.windowHandle().frameMargins()
    decor = QSize(frame.left() + frame.right(), frame.top() + frame.bottom())
    available_client = available.size() - decor
    window.setMinimumSize(QSize(*t.MIN_SIZE).boundedTo(available_client))
    rect = fit_geometry(saved, available, decor)
    window.resize(rect.size())
    window.move(rect.topLeft())
    if settings.value("window/maximized", False, type=bool):
        window.showMaximized()


def save(window: QWidget, settings: QSettings) -> None:
    # QWidget.pos() and move() both use the frame position for top-level windows.
    rect = window.normalGeometry() if window.isMaximized() else QRect(window.pos(), window.size())
    settings.setValue("window/normal", rect)
    settings.setValue("window/maximized", window.isMaximized())
    settings.sync()

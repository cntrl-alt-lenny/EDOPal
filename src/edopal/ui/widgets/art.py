"""Local artwork for hero banners and catalog tiles."""

from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter, QPixmap

ImageSource = str | Path | QPixmap


def pixmap(source: ImageSource | None) -> QPixmap:
    if isinstance(source, QPixmap):
        return source
    return QPixmap(str(source)) if source else QPixmap()


def draw_cover(painter: QPainter, image: QPixmap, target: QRectF) -> None:
    """Center-crop when painting, keeping the original for high-DPI screens and resizes."""
    if image.isNull() or target.isEmpty():
        return
    scale = max(target.width() / image.width(), target.height() / image.height())
    width, height = target.width() / scale, target.height() / scale
    source = QRectF((image.width() - width) / 2, (image.height() - height) / 2, width, height)
    painter.drawPixmap(target, image, source)

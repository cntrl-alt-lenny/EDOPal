from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from edopal.features.formats.deck_view import badge


def test_visible_numeral_is_centered_on_both_axes_at_card_and_legend_sizes(qapp):
    for size in (14, 16, 18, 20, 24, 28, 30):
        for limit in (1, 2):
            sample = QImage(40, 40, QImage.Format.Format_RGB32)
            sample.fill(QColor("black"))
            with QPainter(sample) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                badge(painter, QRectF(3, 3, size, size), limit)
            ink = [
                (x, y)
                for x in range(3, 4 + size)
                for y in range(3, 4 + size)
                if min(sample.pixelColor(x, y).getRgb()[:3]) > 215
            ]
            xs, ys = zip(*ink, strict=True)
            assert min(xs) + max(xs) + 1 == min(ys) + max(ys) + 1 == 6 + size

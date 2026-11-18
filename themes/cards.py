"""Original geometric stand-ins for preview cards. Never included in a skin."""

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPolygonF

from .colors import alpha, mix

NAMES = (
    "Solstice Dial",
    "Glass Meridian",
    "Lunar Gate",
    "Tide Compass",
    "Ember Seal",
    "Vesper Star",
)
_INKS = (
    ("#b98b5c", "#192d39", "#edbd76"),
    ("#539e93", "#183d46", "#9ce0cd"),
    ("#ab8ab8", "#3b2949", "#dbc1eb"),
    ("#829db5", "#1e3c54", "#a5d9e6"),
    ("#c88d6f", "#4c2b30", "#ffb57d"),
    ("#a9a8b2", "#30334c", "#e9d9a6"),
)


@lru_cache(maxsize=8)
def card(index: int) -> QImage:
    backing, dark, light = _INKS[index % 6]
    img = QImage(128, 182, QImage.Format.Format_RGB32)
    img.fill(QColor("#181a20"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(3, 3, 122, 176, QColor(backing))
    p.fillRect(8, 9, 112, 12, QColor(mix(backing, light, 0.6)))
    p.setPen(QPen(QColor(dark), 1.8))
    p.drawLine(12, 15, 62 + index * 4, 15)
    for i in range(3 + index % 4):
        p.setBrush(QColor(light))
        p.drawEllipse(QPointF(114 - i * 7, 26), 2.6, 2.6)
    frame = QRectF(10, 32, 108, 100)
    grad = QLinearGradient(8, 131, 114, 32)
    grad.setColorAt(0, QColor(dark))
    grad.setColorAt(1, QColor(mix(dark, backing, 0.60)))
    p.fillRect(frame, grad)
    p.save()
    p.setClipRect(frame)
    p.translate(64, 82)
    p.setPen(QPen(QColor(alpha(light, 88)), 0.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for radius in (27, 31, 42, 66):
        p.drawEllipse(QPointF(), radius, radius)
    for i in range(24):
        p.drawLine(0, -37, 0, -57 if i % 3 else -67)
        p.rotate(15)
    p.rotate(index * 13)
    points = 3 + index % 4
    verts = [
        QPointF(
            math.sin(i * math.pi / points) * (32 if i % 2 == 0 else 11),
            -math.cos(i * math.pi / points) * (32 if i % 2 == 0 else 11),
        )
        for i in range(points * 2)
    ]
    p.setPen(QPen(QColor(light), 1.3))
    p.setBrush(QColor(mix(light, dark, 0.34)))
    p.drawPolygon(QPolygonF(verts))
    for i in range(0, points * 2, 2):
        p.setBrush(QColor(light))
        p.drawPolygon(QPolygonF([QPointF(), verts[i], verts[i - 1]]))
    p.restore()
    p.fillRect(9, 138, 110, 34, QColor(mix(backing, "#fff2d8", 0.62)))
    p.setPen(QPen(QColor(alpha(dark, 160)), 1.3))
    for y, width in ((145, 88), (150, 98), (155, 76), (165, 26)):
        p.drawLine(14 if y != 165 else 85, y, 14 + width if y != 165 else 111, y)
    p.end()
    return img

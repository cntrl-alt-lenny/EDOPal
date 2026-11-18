"""Paint the archive's foil lines at device resolution around the shipped card art."""

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from studio import theme as t
from studio.icons import icon

# Art coordinates correspond to the three printed cards in the existing 800x212 banner.
FAN = [(451, 78), (551, 47), (550, 18), (695, 11), (697, 42), (799, 77), (759, 212), (493, 212)]
_rng = random.Random(42)
STARS = tuple((_rng.random(), _rng.random(), _rng.uniform(0.5, 1.4)) for _ in range(125))


def _colour(colour: str, alpha: int) -> QColor:
    c = QColor(colour)
    c.setAlpha(alpha)
    return c


def _foil(p: QPainter, x: float, y: float, radius: float) -> None:
    p.save()
    p.translate(x, y)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(_colour(t.GOLD, 60), 1))
    for ratio in (0.69, 0.91, 0.96, 1.15):
        r = radius * ratio
        p.drawEllipse(QPointF(), r, r)
    for angle in range(0, 360, 5):
        p.save()
        p.rotate(angle)
        length = 0.048 if angle % 30 == 0 else 0.016
        p.drawLine(QPointF(radius * 0.96, 0), QPointF(radius * (0.96 + length), 0))
        p.restore()
    p.setPen(QPen(_colour(t.GOLD, 35), 1))
    for angle in (0, 60):
        points = [
            QPointF(
                radius * math.cos(math.radians(a + angle)),
                radius * math.sin(math.radians(a + angle)),
            )
            for a in (-90, 30, 150)
        ]
        p.drawPolygon(QPolygonF(points))
    p.restore()


class ArchiveArt(QWidget):
    def __init__(self, symbol: str = "cards") -> None:
        super().__init__()
        self.symbol = symbol
        self.texture = QPixmap(str(t.ASSETS / "window/studio-vortex.jpg"))
        self.fan = QPixmap(str(t.ASSETS / "window/archive.png")) if symbol == "cards" else QPixmap()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(t.HERO_MIN)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        w, h = self.width(), self.height()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), t.R_PANEL, t.R_PANEL)
        p.setClipPath(clip)
        dark, light = t.FOIL.get(self.symbol, (t.HERO_DARK, t.HERO_LIGHT))
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0, QColor(dark))
        bg.setColorAt(0.65, QColor(light))
        bg.setColorAt(1, QColor(dark))
        p.fillRect(self.rect(), bg)
        glow = QRadialGradient(QPointF(w * 0.77, h * 0.47), h)
        glow.setColorAt(0, _colour(light, 170))
        glow.setColorAt(1, _colour(t.BRONZE, 0))
        p.fillRect(self.rect(), glow)
        p.save()
        p.setOpacity(0.27 if self.symbol == "cards" else 0.10)
        p.drawPixmap(self.rect(), self.texture)
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_colour(t.PALE, 95))
        for x, y, r in STARS:
            p.drawEllipse(QPointF(x * w, y * h), r, r)
        _foil(p, w * 0.78, h * 0.52, h * 0.76)
        if not self.fan.isNull():
            scale = min(w * 0.46 / 350, h * 0.93 / 201)
            p.save()
            p.translate(w * 0.76 - 625 * scale, h - 212 * scale)
            p.scale(scale, scale)
            shape = QPainterPath()
            shape.addPolygon(QPolygonF([QPointF(x, y) for x, y in FAN]))
            shape.closeSubpath()
            p.setClipPath(shape, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(0, 0, self.fan)
            p.restore()
        else:
            self._print(p, w * 0.77, h * 0.49, min(h * 0.62, w * 0.28))
        veil = QLinearGradient(0, 0, w * 0.60, 0)
        veil.setColorAt(0, _colour(dark, 175))
        veil.setColorAt(0.60, _colour(dark, 135))
        veil.setColorAt(1, _colour(dark, 0))
        p.fillRect(self.rect(), veil)
        p.setPen(QPen(_colour(t.GOLD, 80), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), t.R_PANEL, t.R_PANEL)

    def _print(self, p: QPainter, x: float, y: float, height: float) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(9)
        w = height * 0.67
        rect = QRectF(-w / 2, -height / 2, w, height)
        p.setBrush(QColor(t.HERO_DARK))
        p.setPen(QPen(QColor(t.BRONZE), 1.5))
        p.drawRoundedRect(rect.translated(-w * 0.1, height * 0.035), t.R_SMALL, t.R_SMALL)
        p.drawRoundedRect(rect, t.R_SMALL, t.R_SMALL)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(t.SM, t.SM, -t.SM, -t.SM))
        side = int(w * 0.5)
        icon(self.symbol, t.GOLD).paint(p, -side // 2, -side // 2, side, side)
        for direction in (-1, 1):
            p.save()
            p.translate(0, direction * height * 0.33)
            p.rotate(45)
            p.drawRect(QRectF(-2, -2, 4, 4))
            p.restore()
        p.restore()

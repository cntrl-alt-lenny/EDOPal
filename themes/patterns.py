"""Original procedural studies in a 1000 × 625 artboard. Geometry is resolution independent."""

import math
from dataclasses import dataclass
from functools import lru_cache
from random import Random

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
    QTransform,
)

from .colors import alpha, mix

BOARD = QRectF(0, 0, 1000, 625)


@dataclass(frozen=True)
class Pigments:
    ground: str
    surface: str
    accent: str
    counter: str


def _tint(color: str, opacity: int) -> QColor:
    return QColor(alpha(color, opacity))


def _wash(p: QPainter, x: float, y: float, radius: float, color: str, opacity: int) -> None:
    grad = QRadialGradient(x, y, radius)
    grad.setColorAt(0, _tint(color, opacity))
    grad.setColorAt(1, _tint(color, 0))
    p.fillRect(BOARD, grad)


@lru_cache(maxsize=12)
def _nap(seed: int) -> QImage:
    """A small repeat of crossing fibres; tiling avoids pixel loops over a full wallpaper."""
    rng = Random(seed)
    tile = QImage(104, 104, QImage.Format.Format_ARGB32_Premultiplied)
    tile.fill(Qt.GlobalColor.transparent)
    p = QPainter(tile)
    for i in range(1800):
        tone = 255 if i % 2 else 0
        p.setPen(QColor(tone, tone, tone, rng.randrange(6, 19)))
        x, y = rng.randrange(104), rng.randrange(104)
        p.drawLine(x, y, x + rng.randrange(1, 4), y + (1 if i % 3 else -1))
    p.end()
    return tile


def felt(p: QPainter, c: Pigments, rng: Random) -> None:
    _wash(p, 380, 280, 590, c.surface, 180)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.fillRect(BOARD, QBrush(_nap(rng.getrandbits(24))))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for inset in (26, 33):
        pen = QPen(_tint(c.accent, 44 if inset == 26 else 64), 1.3)
        if inset == 33:
            pen.setDashPattern([3, 4])
        p.setPen(pen)
        p.drawRoundedRect(BOARD.adjusted(inset, inset, -inset, -inset), 39, 39)


def starfield(p: QPainter, c: Pigments, rng: Random) -> None:
    # Diffuse clusters follow a diagonal dust lane. Leave open sky between them.
    angle = rng.uniform(-0.13, 0.13)
    for x in range(-120, 1250, 135):
        y = 475 - x * (0.39 + angle) + 26 * math.sin(x / 190)
        _wash(p, x, y, 145, c.counter, 17)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(260):
        x = rng.uniform(0, 1000)
        y = rng.uniform(0, 625) if i % 3 else rng.gauss(475 - x * (0.39 + angle), 40)
        r = rng.uniform(0.55, 1.65)
        p.setBrush(_tint(c.accent if i % 4 else c.counter, rng.randint(45, 146)))
        p.drawEllipse(QPointF(x, y), r, r)
    for x in (rng.randint(72, 210), rng.randint(742, 955)):
        y = rng.randrange(84, 474)
        _wash(p, x, y, 18, c.counter, 55)
        p.setBrush(_tint(c.accent, 180))
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(x + dx, y + dy)
                    for dx, dy in (
                        (0, -8),
                        (1.2, -1.4),
                        (5, 0),
                        (1.2, 1.4),
                        (0, 8),
                        (-1.2, 1.4),
                        (-5, 0),
                        (-1.2, -1.4),
                    )
                ]
            )
        )
    for x, y, bearing in ((80, 140, -8), (750, 430, -23)):
        p.save()
        p.translate(x, y)
        p.rotate(bearing + rng.uniform(-15, 15))
        points = [QPointF(i * 34, rng.uniform(-36, 36)) for i in range(6)]
        p.setPen(QPen(_tint(c.accent, 72), 1))
        p.drawPolyline(QPolygonF(points))
        p.setBrush(_tint(c.counter, 166))
        for i, point in enumerate(points):
            radius = 3.4 if i in (1, 4) else 2
            p.drawEllipse(point, radius, radius)
        p.restore()


def contours(p: QPainter, c: Pigments, rng: Random) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    for x, y, size in ((-25, 526, 284), (923, 115, 326), (600, 728, 118)):
        spin = rng.uniform(0, math.tau)
        # Shared harmonics keep adjacent elevation lines nested.
        rays = []
        for i in range(97):
            a = math.tau * i / 96
            r = 1 + 0.17 * math.sin(3 * a + spin) + 0.09 * math.cos(5 * a - spin)
            rays.append((r * math.cos(a), 0.71 * r * math.sin(a)))
        for i in range(1, 19):
            r = size * i / 17
            cx, cy = x + i * 2.8, y - i * 1.7
            p.setPen(
                QPen(
                    _tint(c.accent if i % 4 else c.counter, 56 if i % 4 else 98),
                    1 if i % 4 else 1.6,
                )
            )
            p.drawPolyline(QPolygonF([QPointF(cx + r * dx, cy + r * dy) for dx, dy in rays]))


def dunes(p: QPainter, c: Pigments, rng: Random) -> None:
    sun_x, sun_y = rng.uniform(145, 260), rng.uniform(110, 150)
    _wash(p, sun_x, sun_y, 155, c.counter, 39)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_tint(mix(c.counter, "#fff2d0", 0.72), 118))
    p.drawEllipse(QPointF(sun_x, sun_y), 26, 26)
    for i in range(5):
        y = 304 + i * 64
        drift = rng.uniform(-65, 65)
        ridge = QPainterPath(QPointF(-10, y + 20))
        ridge.cubicTo(170, y - 25 - drift, 218, y + 62 + drift, 473, y + 30)
        ridge.cubicTo(713, y + 7, 807, y - 166 + i * 16, 1010, y - 74)
        fill = QPainterPath(ridge)
        fill.lineTo(1010, 635)
        fill.lineTo(-10, 635)
        fill.closeSubpath()
        grad = QLinearGradient(0, y - 52, 0, y + 103)
        grad.setColorAt(0, QColor(mix(c.surface, c.accent, 0.24 + 0.032 * i)))
        grad.setColorAt(1, QColor(mix(c.ground, c.surface, 0.22)))
        p.fillPath(fill, grad)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_tint(c.counter, 76), 1.2))
        p.drawPath(ridge)


def petals(p: QPainter, c: Pigments, rng: Random) -> None:
    # Sprigs are drawn in their own coordinates so each leaf joins its stem.
    p.setBrush(Qt.BrushStyle.NoBrush)
    leaf = QPainterPath()
    leaf.cubicTo(4, -8, 14, -9, 20, -6)
    leaf.cubicTo(15, 2, 7, 5, 0, 0)
    for x, y, rotation, scale in ((-27, 575, -23, 1.24), (1035, 1, 158, 1.05)):
        p.save()
        p.translate(x, y)
        p.rotate(rotation + rng.uniform(-6, 6))
        p.scale(scale, scale)
        stem = QPainterPath(QPointF(0, 0))
        stem.cubicTo(106, -31, 153, -129, 353, -111)
        p.setPen(QPen(_tint(c.accent, 98), 2.2))
        p.drawPath(stem)
        for i in range(2, 15):
            u = i / 16
            p.save()
            p.translate(stem.pointAtPercent(u))
            p.rotate(-stem.angleAtPercent(u) + (-55 if i % 2 else 39))
            length = rng.uniform(23, 66) * (1.25 - 0.5 * u)
            twig = QPainterPath()
            twig.quadTo(length * 0.6, 5, length, -5)
            p.setPen(QPen(_tint(c.accent, 101), 1.2))
            p.drawPath(twig)
            for j in range(1, 6):
                p.save()
                p.translate(twig.pointAtPercent(j / 6))
                p.rotate(-35 if j % 2 else 52)
                p.scale(0.55 + j * 0.045, 0.65)
                p.fillPath(leaf, _tint(c.accent if j % 3 else c.counter, 112))
                p.restore()
            p.translate(length, -5)
            for _ in range(5):
                p.rotate(72)
                p.fillPath(leaf, _tint(c.counter, 137))
            p.restore()
        p.restore()


def brushed(p: QPainter, c: Pigments, rng: Random) -> None:
    sheen = QLinearGradient(0, 480, 990, 105)
    for t, opacity in ((0, 6), (0.19, 50), (0.26, 10), (0.59, 3), (0.78, 63), (1, 9)):
        sheen.setColorAt(t, _tint(c.accent, opacity))
    p.fillRect(BOARD, sheen)
    p.setPen(QPen(_tint(c.accent, 29), 0.9))
    scratches = []
    for _ in range(420):
        x, y = rng.uniform(-120, 1000), rng.uniform(0, 625)
        scratches.append(QLineF(x, y, x + rng.uniform(12, 105), y))
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    p.drawLines(scratches)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # A pair of offset inlaid bands frames the brushed metal.
    for origin in (-275, 606):
        for offset, width in ((0, 4.0), (12, 1.2)):
            p.setPen(QPen(_tint(c.counter, 78), width))
            p.drawLine(QPointF(origin + offset, 625), QPointF(origin + offset + 555, 0))


def lattice(p: QPainter, c: Pigments, rng: Random) -> None:
    pitch = rng.randint(45, 58)
    # Rasterize one repeat at 2x. Economy matters here: long diagonal
    # antialiased strokes otherwise cross most of a full-resolution buffer.
    tile = QImage(4 * pitch, 4 * pitch, QImage.Format.Format_ARGB32_Premultiplied)
    tile.fill(Qt.GlobalColor.transparent)
    weave = QPainter(tile)
    weave.scale(2, 2)
    weave.setRenderHint(QPainter.RenderHint.Antialiasing)
    weave.setPen(QPen(_tint(c.accent, 43), 1.3))
    weave.drawLines([QLineF(0, 0, 2 * pitch, 2 * pitch), QLineF(0, 2 * pitch, 2 * pitch, 0)])
    weave.setPen(Qt.PenStyle.NoPen)
    weave.setBrush(_tint(c.counter, 83))
    for x, y in ((pitch, pitch), (0, 0), (2 * pitch, 0), (0, 2 * pitch), (2 * pitch, 2 * pitch)):
        weave.drawPolygon(
            QPolygonF(
                [QPointF(x, y - 4), QPointF(x + 2.5, y), QPointF(x, y + 4), QPointF(x - 2.5, y)]
            )
        )
    weave.end()
    brush = QBrush(tile)
    brush.setTransform(QTransform.fromScale(0.5, 0.5))
    p.setBrushOrigin(QPointF(rng.uniform(0, pitch), 0))
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.fillRect(BOARD, brush)


def facets(p: QPainter, c: Pigments, rng: Random) -> None:
    grid = [
        [
            QPointF(i * 159 + rng.uniform(-48, 48), j * 146 + rng.uniform(-38, 38))
            for i in range(-1, 9)
        ]
        for j in range(-1, 7)
    ]
    p.setPen(Qt.PenStyle.NoPen)
    for row, below in zip(grid[:-1], grid[1:], strict=True):
        for i in range(len(row) - 1):
            a, b, d, e = row[i], row[i + 1], below[i], below[i + 1]
            for tri in ((a, b, d), (d, b, e)):
                cx = sum(pt.x() for pt in tri) / 3
                intensity = 0.24 + 0.76 * min(1, abs(cx - 500) / 425)
                color = c.accent if rng.random() < 0.68 else c.counter
                p.setBrush(_tint(color, round(rng.uniform(11, 97) * intensity)))
                p.drawPolygon(QPolygonF(list(tri)))


PATTERNS = {
    "felt": felt,
    "starfield": starfield,
    "contours": contours,
    "dunes": dunes,
    "petals": petals,
    "brushed": brushed,
    "lattice": lattice,
    "facets": facets,
}

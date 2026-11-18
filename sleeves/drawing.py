"""Small QPainter primitives shared by the engraving plates."""

import math
from collections.abc import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF

Point = tuple[float, float]
Inks = tuple[QColor, ...]
NONE = Qt.PenStyle.NoPen
CLEAR = Qt.BrushStyle.NoBrush


def mix(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor(
        *(round(x + (y - x) * amount) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3], strict=True))
    )


def pen(c: QColor, width: float = 1) -> QPen:
    result = QPen(c)
    result.setWidthF(width)
    result.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    result.setCapStyle(Qt.PenCapStyle.RoundCap)
    return result


def polar(radius: float, angle: float) -> Point:
    return radius * math.cos(angle), radius * math.sin(angle)


def contour(
    p: QPainter,
    start: Point,
    segments: Iterable[tuple[float, ...]],
    fill: QColor | QBrush | None = None,
    edge: QColor | None = None,
    width: float = 1,
) -> None:
    """Ink a silhouette from straight endpoints and cubic Bézier segments."""
    outline = QPainterPath(QPointF(*start))
    for segment in segments:
        if len(segment) == 2:
            outline.lineTo(*segment)
        else:
            outline.cubicTo(*segment)
    if fill is not None:
        outline.closeSubpath()
    p.setBrush(CLEAR if fill is None else fill)
    p.setPen(NONE if edge is None else pen(edge, width))
    p.drawPath(outline)


def path(p: QPainter, pts: Iterable[Point], ink: QColor, width: float = 1) -> None:
    p.setPen(pen(ink, width))
    p.setBrush(CLEAR)
    p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))


def polygon(p: QPainter, pts: Iterable[Point], fill: QColor, edge: QColor | None = None) -> None:
    p.setPen(NONE if edge is None else pen(edge))
    p.setBrush(fill)
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))


def oval(
    p: QPainter,
    x: float,
    y: float,
    rx: float,
    ry: float,
    fill: QColor | None,
    edge: QColor | None = None,
    width: float = 1,
) -> None:
    p.setPen(NONE if edge is None else pen(edge, width))
    p.setBrush(CLEAR if fill is None else fill)
    p.drawEllipse(QRectF(x - rx, y - ry, rx * 2, ry * 2))


def star(x: float, y: float, radius: float, inner: float, points: int) -> list[Point]:
    return [
        (x + dx, y + dy)
        for i in range(points * 2)
        for dx, dy in [polar(inner if i % 2 else radius, math.pi * i / points - math.pi / 2)]
    ]

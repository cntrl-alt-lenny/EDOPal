"""Procedural wallpapers authored in a common 1000 × 625 drawing space."""

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

from .colors import alpha, mix
from .imaging import ensure_gui
from .model import Theme


def _glow(p: QPainter, x: int, y: int, r: int, color: str, strength: int) -> None:
    gradient = QRadialGradient(x, y, r)
    gradient.setColorAt(0, QColor(alpha(color, strength)))
    gradient.setColorAt(1, QColor(alpha(color, 0)))
    p.fillRect(QRectF(x - r, y - r, 2 * r, 2 * r), gradient)


def _ribbon(i: int) -> QPainterPath:
    curve = QPainterPath(QPointF(-80, 530 + i * 7))
    curve.cubicTo(230, 450 + i * 12, 270 + i * 3, 110 - i * 5, 614, 275 + i * 2)
    curve.cubicTo(780, 345 + i * 5, 810 + i * 7, 138, 1060, 66 + i * 9)
    return curve


def _procedural(
    bg: str, surface: str, accent: str, style: str, w: int, h: int, stretch: bool
) -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    try:
        sx, sy = w / 1000, h / 625
        if not stretch:
            sx = sy = max(sx, sy)
        p.translate((w - 1000 * sx) / 2, (h - 625 * sy) / 2)
        p.scale(sx, sy)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QLinearGradient(60, 610, 890, 40)
        base.setColorAt(0, QColor(bg))
        base.setColorAt(0.55, QColor(mix(bg, surface, 0.62)))
        base.setColorAt(1, QColor(mix(surface, accent, 0.26)))
        p.fillRect(QRectF(0, 0, 1000, 625), base)
        _glow(p, 760, 165, 470, accent, 95)
        _glow(p, 210, 540, 430, accent, 44)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if style == "gradient":
            _glow(p, 590, 530, 365, mix(accent, "#ffe2b9", 0.55), 66)
        elif style == "aurora":
            # Two translucent curtains concentrate the light at their lower edge.
            for shift, tint in ((-42, mix(accent, "#afa0ff", 0.48)), (0, accent)):
                curtain = QPainterPath(QPointF(-80, 555 + shift))
                curtain.cubicTo(222, 468 + shift, 274, 94 + shift, 614, 274 + shift)
                curtain.cubicTo(784, 348 + shift, 850, 139 + shift, 1060, 62 + shift)
                curtain.lineTo(1060, -40)
                curtain.cubicTo(791, 14 + shift, 841, 274 + shift, 625, 233 + shift)
                curtain.cubicTo(277, 99 + shift, 200, 352 + shift, -80, 482 + shift)
                curtain.closeSubpath()
                veil = QLinearGradient(160, 110, 710, 410)
                veil.setColorAt(0, QColor(alpha(tint, 4)))
                veil.setColorAt(0.64, QColor(alpha(tint, 43)))
                veil.setColorAt(1, QColor(alpha(tint, 11)))
                p.fillPath(curtain, veil)
            for i in range(-12, 14):
                intensity = math.exp(-abs(i) * 0.18)
                color = mix(accent, "#c1ffff", 0.45 * intensity)
                p.setPen(QPen(QColor(alpha(color, int(82 * intensity) + 8)), 1.0 if i % 3 else 2.2))
                p.drawPath(_ribbon(i))
            _glow(p, 615, 266, 170, accent, 62)
        elif style == "vignette":
            # Offset rings and radial graduation marks read like an astrolabe.
            p.save()
            p.translate(822, 164)
            _glow(p, 0, 0, 103, accent, 100)
            disc = QRadialGradient(-32, -34, 121)
            disc.setColorAt(0, QColor(mix(surface, accent, 0.58)))
            disc.setColorAt(0.76, QColor(mix(bg, surface, 0.60)))
            disc.setColorAt(1, QColor(bg))
            p.setBrush(disc)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(), 64, 64)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for radius in (68, 78, 126, 164, 168, 233, 268, 324):
                p.setPen(QPen(QColor(alpha(accent, 78 if radius in (78, 168) else 40)), 1.1))
                p.drawEllipse(QPointF(), radius, radius)
            p.setPen(QPen(QColor(alpha(accent, 105)), 1.4))
            for i in range(72):
                p.drawLine(QPointF(0, -165), QPointF(0, -175 if i % 6 else -184))
                p.rotate(5)
            p.restore()
            shade = QRadialGradient(630, 220, 750)
            shade.setColorAt(0, QColor(alpha(bg, 0)))
            shade.setColorAt(1, QColor(alpha(bg, 210)))
            p.fillRect(QRectF(0, 0, 1000, 625), shade)
        elif style == "geometry":
            for i in range(-3, 8):
                x = i * 132
                path = QPainterPath(QPointF(x - 80, 640))
                path.lineTo(x + 135, 212)
                path.lineTo(x + 316, -20)
                path.lineTo(x + 266, 216)
                path.closeSubpath()
                facet = QLinearGradient(x + 260, 100, x, 590)
                facet.setColorAt(0, QColor(alpha(accent, 72)))
                facet.setColorAt(1, QColor(alpha(accent, 3)))
                p.fillPath(path, facet)
                p.setPen(QPen(QColor(alpha(accent, 58)), 0.9))
                p.drawPath(path)
    finally:
        p.end()
    return img


_preview_wallpaper = lru_cache(maxsize=8)(_procedural)


def crop(source: QRectF, dest: QRectF, stretch: bool) -> QRectF:
    if stretch:
        return source
    ratio = dest.width() / dest.height()
    w = min(source.width(), source.height() * ratio)
    h = min(source.height(), source.width() / ratio)
    return QRectF(source.center().x() - w / 2, source.center().y() - h / 2, w, h)


def wallpaper(
    theme: Theme,
    size: tuple[int, int] = (2560, 1600),
    *,
    image: QImage | None = None,
    scale_background: bool = True,
) -> QImage:
    """Paint the exported canvas, or its viewport crop. Inherit may take a game backdrop."""
    ensure_gui()
    w, h = size
    if min(size) <= 0 or max(size) > 16384 or w * h > 24_000_000:
        raise ValueError("Invalid wallpaper dimensions.")
    canvas = QRectF(0, 0, 2560, 1600)
    viewport = QRectF(0, 0, w, h)
    if theme.wallpaper not in ("inherit", "image"):
        paint = _preview_wallpaper if w * h <= 1_000_000 else _procedural
        return QImage(
            paint(
                theme.background,
                theme.surface,
                theme.accent,
                theme.wallpaper,
                w,
                h,
                scale_background,
            )
        )
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(theme.background))
    if image is None or image.isNull():
        if theme.wallpaper == "image":
            raise ValueError("The selected wallpaper image is missing.")
        return img
    source = QRectF(image.rect())
    if theme.wallpaper == "image":
        source = crop(source, canvas, scale_background)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(viewport, image, crop(source, viewport, scale_background))
    p.end()
    return img

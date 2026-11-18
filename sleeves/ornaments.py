"""Finishing details in a 354 × 508 design space."""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from .catalog import EMBLEMS, Emblem
from .drawing import CLEAR, Inks, mix, oval, path, pen, polar, polygon, star
from .imaging import colour


def draw_frame(p: QPainter, style: str, c: Inks) -> None:
    if style == "none":
        return
    paper, shade, ink, foil = c
    p.setBrush(CLEAR)
    p.setPen(pen(paper, 12))
    p.drawRect(QRectF(0, 0, 354, 508))
    p.setPen(pen(ink, 1.8))
    p.drawRect(QRectF(6, 6, 342, 496))
    if style == "keyline":
        return
    p.setPen(pen(foil, 0.9))
    p.drawRect(QRectF(11, 11, 332, 486))
    for x, y, sx, sy in [(0, 0, 1, 1), (354, 0, -1, 1), (0, 508, 1, -1), (354, 508, -1, -1)]:
        p.save()
        p.translate(x, y)
        p.scale(sx, sy)
        if style == "deco":
            polygon(
                p, [(7, 7), (70, 7), (54, 15), (30, 15), (15, 30), (15, 54), (7, 70)], shade, ink
            )
            path(p, [(52, 10), (26, 10), (10, 26), (10, 52)], foil, 1.6)
            polygon(p, [(15, 15), (25, 14), (14, 25)], foil)
        else:
            oval(p, 17, 17, 8, 8, paper, ink)
            polygon(p, star(17, 17, 6, 2, 4), foil)
            for k in range(4):
                a = 32 + k * 7
                path(p, [(a, 7), (a, 12 + (k == 0) * 4)], ink)
                path(p, [(7, a), (12 + (k == 0) * 4, a)], ink)
        p.restore()


def draw_emblem(p: QPainter, emblem: Emblem, c: Inks) -> None:
    if (
        emblem.shape not in EMBLEMS
        or not 0.08 <= emblem.size <= 0.60
        or not -180 <= emblem.rotation <= 180
    ):
        raise ValueError("Choose a valid emblem, size (0.08–0.60) and rotation (−180° to 180°).")
    paper, shade, ink, foil = c
    fill = colour(emblem.fill) if emblem.fill else foil
    outline = colour(emblem.outline) if emblem.outline else ink
    p.save()
    p.translate(177, 254)
    p.rotate(emblem.rotation)
    p.scale(354 * emblem.size / 100, 354 * emblem.size / 100)
    oval(p, 0, 0, 50, 50, paper, outline, 2.2)
    oval(p, 0, 0, 44, 44, shade, outline)
    if emblem.shape == "diamond":
        pts = [(0, -32), (24, 0), (0, 32), (-24, 0)]
        polygon(p, pts, fill, outline)
        path(p, [(0, -29), (-7, 0), (0, 29), (7, 0), (0, -29)], shade, 1.8)
    elif emblem.shape == "crescent":
        disc, shadow = QPainterPath(), QPainterPath()
        disc.addEllipse(QRectF(-26, -26, 52, 52))
        shadow.addEllipse(QRectF(-9, -36, 47, 51))
        p.setPen(pen(outline))
        p.setBrush(fill)
        p.drawPath(disc.subtracted(shadow))
        polygon(p, star(19, -14, 8, 2.5, 4), fill)
    elif emblem.shape == "shield":
        polygon(
            p,
            [(-23, -24), (0, -18), (23, -24), (21, 5), (14, 19), (0, 31), (-14, 19), (-21, 5)],
            fill,
            outline,
        )
        path(p, [(0, -15), (0, 23)], shade, 2.3)
    elif emblem.shape == "lotus":
        for angle in (-55, -27, 0, 27, 55):
            p.save()
            p.translate(0, 17)
            p.rotate(angle)
            leaf = QPainterPath()
            leaf.cubicTo(-12, -11, -10, -28, 0, -44)
            leaf.cubicTo(10, -28, 12, -11, 0, 0)
            p.setBrush(fill)
            p.setPen(pen(shade, 1.3))
            p.drawPath(leaf)
            p.restore()
    elif emblem.shape == "star":
        polygon(p, star(0, 0, 32, 13, 5), fill, outline)
        polygon(p, star(0, 0, 17, 7, 5), shade)
    else:
        for k in range(16):
            a = math.tau * k / 16
            path(p, [polar(21, a), polar(28 + (k % 2) * 5, a)], fill, 2)
        oval(p, 0, 0, 15, 15, fill, outline)
    p.restore()


def _art_inks(source: QImage) -> Inks:
    thumb = source.scaled(
        12, 12, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    samples = [thumb.pixelColor(x, y) for x in range(12) for y in range(12)]
    samples.sort(key=lambda c: c.lightnessF())
    accent = max(samples[32:112], key=lambda c: c.saturationF())
    return (
        mix(samples[16], QColor("#101722"), 0.72),
        mix(accent, QColor("#202831"), 0.64),
        mix(accent, QColor("#e2bd83"), 0.42),
        mix(samples[125], QColor("#fff0c8"), 0.68),
    )


def art_frame(p: QPainter, source: QImage, palette: tuple[str, ...]) -> QRectF:
    from .designer import inks

    c = inks(palette) if palette else _art_inks(source)
    paper, shade, ink, foil = c
    p.save()
    p.scale(2, 2)
    p.fillRect(0, 0, 354, 508, paper)
    ratio = min(304 / source.width(), 383 / source.height())
    w, h = source.width() * ratio, source.height() * ratio
    art = QRectF((354 - w) / 2, (508 - h) / 2, w, h)
    for top in (True, False):
        p.save()
        if not top:
            p.translate(0, 508)
            p.scale(1, -1)
        y = art.y() / 2 + 4
        r = min(22.0, (y - 13) * 0.65)
        for k in range(15):
            a = math.tau * k / 14
            path(
                p,
                [
                    (177 + math.cos(a) * (r + 4), y + math.sin(a) * (r + 4)),
                    (177 + math.cos(a) * (r + 10), y + math.sin(a) * (r + 10)),
                ],
                ink,
                0.85,
            )
        for x, side in [(27, 1), (327, -1)]:
            for k in range(3):
                path(
                    p,
                    [(x, y - 5 + k * 5), (177 - side * (r + 20 + k * 7), y - 5 + k * 5)],
                    shade,
                    1,
                )
        oval(p, 177, y, r, r, None, ink)
        polygon(p, star(177, y, r * 0.8, r * 0.22, 4), foil)
        p.restore()
    p.setBrush(CLEAR)
    for delta, col in ((5, ink), (2, shade)):
        p.setPen(pen(col, 1.4))
        p.drawRect(art.adjusted(-delta, -delta, delta, delta))
    draw_frame(p, "deco", c)
    p.restore()
    return QRectF(art.x() * 2, art.y() * 2, w * 2, h * 2)

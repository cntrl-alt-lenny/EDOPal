"""Paper, cloth and reflective finishes. Light comes from the recipe's own inks."""

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QBrush, QLinearGradient, QPainter

from .drawing import Inks, contour, mix, oval, path, polygon


def damask(p: QPainter, c: Inks, density: float, fruit: float, extent: float) -> None:
    paper, shade, ink, foil = c
    period = 117 / density
    rx, ry = math.ceil(extent / period) + 1, math.ceil(extent / (period * 1.3)) + 1
    for row in range(-ry, ry + 1):
        for col in range(-rx, rx + 1):
            p.save()
            p.translate((col + 0.5 * (row % 2)) * period, row * period * 1.3)
            p.scale(period / 117, period / 117)
            for side in (-1, 1):
                p.save()
                p.scale(side, 1)
                contour(
                    p,
                    (0, 67),
                    [(12, 49, 42, 43, 50, 18), (62, -19, 27, -39, 0, -67)],
                    edge=shade,
                    width=4,
                )
                contour(
                    p,
                    (2, 43),
                    [(19, 23, 49, 16, 39, -5), (25, -19, 22, 2, 31, 3), (20, 8, 14, 17, 9, 25)],
                    ink,
                )
                p.restore()
            contour(
                p,
                (0, 17),
                [
                    (-fruit * 1.35, 6, -fruit, -15, -8, -22),
                    (-12, -32),
                    (-3, -28),
                    (0, -37),
                    (4, -28),
                    (12, -32),
                    (8, -22),
                    (fruit, -15, fruit * 1.35, 6, 0, 17),
                ],
                mix(ink, foil, 0.28),
            )
            for x, y in [(-6, -8), (5, -11), (0, 0)]:
                oval(p, x, y, 2, 3, paper)
            p.restore()


def tartan(p: QPainter, c: Inks, density: float, band: float, extent: float) -> None:
    paper, shade, ink, foil = c
    period = 110 / density
    count = math.ceil(extent / period) + 1
    for turn in (0, 90):
        p.save()
        p.rotate(turn)
        for j in range(-count, count + 1):
            x = j * period
            for offset, width, tone in [
                (-band * 0.6, band, shade),
                (band * 0.57, 4, ink),
                (-period * 0.35, 8, mix(shade, ink, 0.55)),
                (-period * 0.47, 2.5, foil),
            ]:
                paint = tone.toRgb()
                paint.setAlpha(188 if turn else 255)
                p.fillRect(QRectF(x + offset, -extent, width, 2 * extent), paint)
        p.restore()
    for j in range(-count, count + 1):
        for k in range(-count, count + 1):
            x, y = j * period - band * 0.60, k * period - band * 0.60
            for i in range(0, round(band), 5):
                path(p, [(x + i, y), (x, y + i)], mix(shade, ink, 0.55), 1.2)


def marble(p: QPainter, c: Inks, density: float, drift: float, extent: float) -> None:
    paper, shade, ink, foil = c
    # Floating ink is combed laterally. Successive contours follow the same
    # displacement field, so neighbouring ribbons retain their fine cracks.
    interval = 43 / density
    edge = math.ceil(extent)
    for k in range(-math.ceil((edge + 85) / interval), math.ceil((edge + 85) / interval) + 1):
        base = k * interval
        for offset, width, tone in [(0, 14, shade), (9, 3, ink), (-9, 1.6, foil)]:
            pts = []
            for y in range(-edge - 5, edge + 6, 9 if p.device().height() <= 254 else 5):
                wave = drift * (
                    math.sin(0.016 * y + 0.011 * base) + 0.55 * math.sin(0.032 * y - 0.018 * base)
                )
                fold = (
                    43 * math.tanh((y - 55 * math.sin(base * 0.021)) / 25) * math.cos(base * 0.016)
                )
                pts.append((base + offset + wave + fold, y))
            path(p, pts, tone, width)


def timber(p: QPainter, c: Inks, density: float, knot: float, extent: float) -> None:
    paper, shade, ink, foil = c
    p.rotate(-13)
    edge = math.ceil(extent)
    interval = 10 / density
    for j in range(-math.ceil(edge / interval) - 4, math.ceil(edge / interval) + 5):
        x = j * interval
        pts = []
        for y in range(-edge - 5, edge + 6, 8):
            dx = x - 29
            shift = (32 * knot if dx >= 0 else -32 * knot) * math.exp(
                -abs(dx) / 65 - ((y + 46) / 76) ** 2
            )
            pts.append((x + shift + 3 * math.sin(y * 0.013 + j * 0.31), y))
        if j % 5 == 0:
            path(p, pts, mix(shade, ink, 0.43), 5.5)
        path(p, pts, ink if j % 5 == 0 else mix(shade, ink, 0.22), 2 if j % 3 == 0 else 1.4)
    for j in range(6, 0, -1):
        r = j / 6
        contour(
            p,
            (29, -46 - 78 * r),
            [
                (
                    29 + 43 * knot * r,
                    -46 - 8 * r,
                    29 + 34 * knot * r,
                    -46 + 63 * r,
                    29,
                    -46 + 77 * r,
                ),
                (
                    29 - 31 * knot * r,
                    -46 + 57 * r,
                    29 - 38 * knot * r,
                    -46 + 7 * r,
                    29,
                    -46 - 78 * r,
                ),
            ],
            paper if j % 2 else shade,
            ink,
            1.4,
        )


def alloy(p: QPainter, c: Inks, density: float, glint: float, extent: float) -> None:
    paper, shade, ink, foil = c
    grad = QLinearGradient(-180, -180, 180, 170)
    for t, col in [(0, shade), (glint * 0.72, ink), (glint, foil), (glint + 0.12, ink), (1, shade)]:
        grad.setColorAt(t, col)
    p.fillRect(QRectF(-extent, -extent, 2 * extent, 2 * extent), grad)
    edge = math.ceil(extent)
    for i in range(round(380 * density)):
        y = (i * 0.618033989 % 1) * 2 * edge - edge
        x = (i * 0.414213562 % 1) * 2 * edge - edge
        tone = paper.toRgb() if i % 3 else foil.toRgb()
        tone.setAlpha(69)
        path(p, [(x, y), (x + 13 + i % 82, y)], tone, 0.8)
    for x in (-142, 142):
        for y in (-215, 215):
            oval(p, x, y, 5.6, 5.6, shade, foil, 1)
            path(p, [(x - 2, y + 2), (x + 2, y - 2)], paper, 1.6)


def leather(p: QPainter, c: Inks, density: float, seam: float, extent: float) -> None:
    paper, shade, ink, foil = c
    edge = math.ceil(extent)
    # The lower panel is quilted on the bias behind a smooth buttoned flap.
    for j in range(-math.ceil(edge / 42) * 2, math.ceil(edge / 42) * 2 + 1):
        for side in (-1, 1):
            y = j * 47 / density
            path(p, [(-edge, y - side * edge), (edge, y + side * edge)], mix(shade, ink, 0.14), 1.2)
    contour(
        p,
        (-extent, -extent),
        [
            (extent, -extent),
            (extent, -19),
            (138, -22, 62, seam, 0, seam + 8),
            (-62, seam, -138, -22, -extent, -19),
        ],
        shade,
        ink,
        2.8,
    )

    # Short saddle stitches run parallel to the curved flap.
    def stitch_y(pos: float) -> float:
        return seam - 6 - (seam + 15) * math.sin(min(abs(pos) / 202, 1) * math.pi / 2) ** 1.6

    for j in range(-round(17 * density), round(17 * density) + 1):
        x = j * 12 / density
        path(p, [(x, stitch_y(x)), (x + 6, stitch_y(x + 6))], foil, 1.7)
    oval(p, 0, seam - 19, 11, 11, paper, ink, 2)
    oval(p, -1, seam - 20, 6, 6, ink, foil)


def facetfoil(p: QPainter, c: Inks, density: float, offset: float, extent: float) -> None:
    paper, shade, ink, foil = c
    step = 89 / density
    inverse, _ = p.deviceTransform().inverted()
    visible = inverse.mapRect(QRectF(p.device().rect()))
    # Allow one extra facet on each side for the displaced vertices.
    columns = range(math.floor(visible.left() / step) - 1, math.ceil(visible.right() / step) + 1)
    rows = range(math.floor(visible.top() / step) - 1, math.ceil(visible.bottom() / step) + 1)

    def vertex(i: int, j: int) -> tuple[float, float]:
        return (
            step * (i + offset * math.sin(i * 8 + j * 3)),
            step * (j + offset * 0.69 * math.sin(i * 4 - j * 9)),
        )

    for i in columns:
        for j in rows:
            a, b, d, e = vertex(i, j), vertex(i + 1, j), vertex(i, j + 1), vertex(i + 1, j + 1)
            for k, triangle in enumerate(((a, b, d), (d, b, e))):
                t = (math.sin(i * 19 + j * 37 + k * 11) + 1) / 2
                grad = QLinearGradient(QPointF(*triangle[0]), QPointF(*triangle[2]))
                grad.setColorAt(0, mix(shade, ink, t))
                grad.setColorAt(0.57, mix(ink, foil, t))
                grad.setColorAt(1, mix(shade, paper, 0.5))
                contour(p, triangle[0], triangle[1:], QBrush(grad), shade, 0.65)


def crane(p: QPainter, c: Inks, density: float, lift: float, extent: float) -> None:
    paper, shade, ink, foil = c
    # Edges are shared by the folded facets, including the valley fold beneath the neck.
    tip = (13, -139 * lift)
    facets = [
        ([(-67, 45), (-148, -28), (-36, 1)], shade),
        ([(-67, 45), (-36, 1), (6, 40)], ink),
        ([(-67, 45), tip, (-19, 4)], foil),
        ([(-67, 45), (-19, 4), (19, 48)], mix(foil, ink, 0.45)),
        ([(-19, 4), tip, (19, 48)], ink),
        ([(19, 48), (89, -65), (108, -71), (61, 19)], foil),
        ([(89, -65), (110, -74), (139, -60), (108, -60)], ink),
        ([(-67, 45), (19, 48), (61, 19), (1, 77)], shade),
    ]
    for pts, tone in facets:
        polygon(p, pts, tone, paper)
    for j in range(round(4 * density)):
        y = 96 + j * 8
        w = 54 - j * 9
        path(p, [(-w, y), (w, y)], shade, 1.5)


def plain(p: QPainter, c: Inks, density: float, margin: float, extent: float) -> None:
    paper, shade, ink, foil = c
    p.fillRect(QRectF(-extent, -extent, 2 * extent, 2 * extent), paper)
    w, h = 177 - margin, 254 - margin
    path(p, [(-w, -h), (w, -h), (w, h), (-w, h), (-w, -h)], ink, 1.5 * density)


MATERIALS = {
    "damask": damask,
    "tartan": tartan,
    "marble": marble,
    "timber": timber,
    "alloy": alloy,
    "leather": leather,
    "facetfoil": facetfoil,
    "crane": crane,
    "plain": plain,
}

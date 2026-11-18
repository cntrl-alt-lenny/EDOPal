"""Drafting-room graphics and type cut directly as outlines, independent of system fonts."""

import math

from PySide6.QtCore import QRectF
from PySide6.QtGui import QConicalGradient, QPainter

from .drawing import CLEAR, NONE, Inks, contour, mix, oval, path, pen, polygon, star


def chip(p: QPainter, c: Inks, density: float, die: float, extent: float) -> None:
    paper, shade, ink, foil = c
    p.rotate(-12)
    for side in range(4):
        p.save()
        p.rotate(side * 90)
        n = round(8 * density)
        for j in range(n):
            x = (j + 0.5 - n / 2) * 2 * die / n
            y = -die - 15 - (j * 17 + side * 21) % 42
            direction = 1 if j >= n / 2 else -1
            end = x + direction * (16 + j * 3)
            trace = [(x, -die), (x, y), (end, y - abs(end - x)), (end, y - 45 - (j * 23) % 80)]
            path(p, trace, ink if j % 3 == 1 else shade, 2.4)
            oval(p, *trace[-1], 3.6, 3.6, paper, ink, 1.5)
            path(p, [(x, -die - 3), (x, -die - 10)], foil, 2.4)
        p.restore()
    p.fillRect(QRectF(-die, -die, 2 * die, 2 * die), ink)
    p.fillRect(QRectF(5 - die, 5 - die, 2 * die - 10, 2 * die - 10), shade)
    for x, y, w, h in [
        (-0.68, -0.68, 0.75, 0.94),
        (0.16, -0.68, 0.49, 0.49),
        (0.16, -0.08, 0.49, 0.68),
        (-0.68, 0.38, 0.75, 0.22),
    ]:
        p.fillRect(QRectF(x * die, y * die, w * die, h * die), mix(ink, foil, 0.17))


def scope(p: QPainter, c: Inks, density: float, gain: float, extent: float) -> None:
    paper, shade, ink, foil = c
    spacing = 30 / density
    for j in range(-math.ceil(extent / spacing), math.ceil(extent / spacing) + 1):
        n = j * spacing
        path(p, [(n, -extent), (n, extent)], shade, 0.9)
        path(p, [(-extent, n), (extent, n)], shade, 0.9)
    pts = [
        (-extent, 27),
        (-108, 27),
        (-89, 15),
        (-75, 27),
        (-47, 27),
        (-33, 42),
        (-16, -gain),
        (1, gain * 0.73),
        (15, -3),
        (30, 24),
        (49, 24),
        (69, 8),
        (90, 23),
        (extent, 23),
    ]
    path(p, pts, mix(shade, ink, 0.6), 8)
    path(p, pts, foil, 2.8)
    for x, y in [(-16, -gain), (1, gain * 0.73)]:
        oval(p, x, y, 5, 5, ink, foil)


def pianola(p: QPainter, c: Inks, density: float, length: float, extent: float) -> None:
    paper, shade, ink, foil = c
    p.rotate(-8)
    p.fillRect(QRectF(-129, -extent, 258, extent * 2), ink)
    for x in (-119, 119):
        path(p, [(x, -extent), (x, extent)], shade, 1.4)
    step = 24 / density
    count = math.ceil(extent / step) + 2
    melody = (0, 2, 4, 7, 4, 3, 2, 0, -2, 0, 3, 5)
    for i in range(-count, count):
        y = i * step
        notes = [(melody[i % 12], 0.8)]
        if i % 4 in (0, 2):
            root = -7 + i // 4 % 3
            notes.extend(((root, 1.5), (root + 3, 0.76), (root + 7, 0.95)))
        for pitch, duration in notes:
            x = pitch * 12 - 6
            p.fillRect(QRectF(x, y, 9, step * length * duration), paper)
        for x in (-105, 103):
            oval(p, x, y, 3.4, 3.4, paper)
        if i % 4 == 0:
            path(p, [(-92, y - 3), (-69, y - 3)], shade, 1.6)


def edition(p: QPainter, c: Inks, density: float, weight: float, extent: float) -> None:
    paper, shade, ink, foil = c
    # High-contrast display figures with bracketed serifs. Counter and stem widths
    # remain independently visible even when the whole sleeve is only 100px high.
    oval(p, -54, 0, 52, 82, ink)
    oval(p, -54, 0, 52 - 22 * weight, 75, paper)
    w = 22 * weight
    contour(
        p,
        (32, -54),
        [
            (55, -61, 68, -72, 76, -83),
            (81, -83),
            (81, 65),
            (81, 73, 92, 75, 103, 75),
            (103, 81),
            (35, 81),
            (35, 75),
            (50, 75, 81 - w, 72, 81 - w, 64),
            (81 - w, -59),
            (36, -48),
        ],
        foil,
    )
    for side in (-1, 1):
        y = side * 112
        path(p, [(-114, y), (114, y)], ink, 2.5)
        for j in range(round(5 * density)):
            x = -113 + j * 9
            path(p, [(x, y + side * 6), (x, y + side * 13)], shade, 2)


def ampersand(p: QPainter, c: Inks, density: float, tail: float, extent: float) -> None:
    paper, shade, ink, foil = c
    contour(
        p,
        (tail, 82),
        [
            (tail - 22, 103, tail - 36, 97, 22, 65),
            (-4, 96, -69, 109, -84, 66),
            (-99, 27, -68, 3, -34, -17),
            (-75, -65, -48, -111, -6, -108),
            (45, -105, 48, -59, 6, -34),
            (-1, -28, -8, -25, -16, -21),
            (3, 5, 20, 24, 37, 44),
            (48, 26, 53, 10, 48, 1),
            (43, -6, 33, -8, 27, -8),
            (27, -13),
            (87, -13),
            (87, -8),
            (67, -6, 62, 17, 44, 50),
            (tail - 15, 82, tail - 11, 82, tail, 73),
        ],
        ink,
    )
    contour(
        p,
        (-24, -33),
        [
            (-5, -42, 17, -61, 13, -80),
            (9, -105, -24, -103, -30, -81),
            (-36, -63, -30, -47, -24, -33),
        ],
        paper,
    )
    contour(p, (-28, -9), [(-53, 6, -72, 26, -60, 55), (-47, 81, -11, 80, 17, 60)], paper)
    for sign in (-1, 1):
        for k in range(round(3 * density)):
            oval(p, k * 10 - 10, sign * 141, 2, 2, foil)


def velodrome(p: QPainter, c: Inks, density: float, lanes: float, extent: float) -> None:
    paper, shade, ink, foil = c
    p.rotate(32)
    n = round(lanes)
    for i in range(n, -1, -1):
        r = 36 + i * 10
        p.setPen(pen(foil if i in (0, n) else shade, 2))
        p.setBrush(mix(shade, ink, (i % 2) * 0.27 + 0.36) if i else paper)
        p.drawRoundedRect(QRectF(-r, -72 - r, 2 * r, 144 + 2 * r), r, r)
    for i in range(n):
        x = 40 + i * 10
        for j in range(round(4 * density)):
            y = 25 + j * 5
            p.fillRect(QRectF(x - 4, y, 8, 4), foil if (i + j) % 2 else paper)


def spire(p: QPainter, c: Inks, density: float, crown: float, extent: float) -> None:
    paper, shade, ink, foil = c
    for side in (-1, 1):
        p.save()
        p.scale(side, 1)
        for j in range(4):
            w = 149 - j * 26
            roof = 70 - j * 32
            polygon(
                p,
                [
                    (w, extent),
                    (w, roof),
                    (w - 13, roof),
                    (w - 13, roof - 12),
                    (w - 28, roof - 12),
                    (w - 28, extent),
                ],
                mix(shade, paper, j * 0.11),
                ink,
            )
            for i in range(round(3 * density)):
                x = w - 5 - i * 4
                path(p, [(x, roof + 9), (x, extent)], ink, 0.9)
        p.restore()
    polygon(
        p,
        [
            (-44, extent),
            (-44, -29),
            (-34, -29),
            (-34, -54 * crown),
            (-22, -54 * crown),
            (-22, -85 * crown),
            (0, -131 * crown),
            (22, -85 * crown),
            (22, -54 * crown),
            (34, -54 * crown),
            (34, -29),
            (44, -29),
            (44, extent),
        ],
        shade,
        foil,
    )
    polygon(p, [(0, -130 * crown), (22, -85 * crown), (22, extent), (0, extent)], ink)
    for j in range(4):
        y = (-69 + j * 18) * crown
        path(p, [(-21, y + 17), (0, y), (21, y + 17)], foil, 2)
    path(p, [(0, -119 * crown), (0, -163 * crown)], foil, 2.5)
    polygon(p, star(0, -170 * crown, 8, 2.7, 4), foil)


def marquetry(p: QPainter, c: Inks, density: float, pitch: float, extent: float) -> None:
    paper, shade, ink, foil = c
    step = 66 / density
    edge = math.ceil(extent)
    for j in range(-math.ceil(2 * edge / step), math.ceil(2 * edge / step) + 1):
        y = j * step
        for side in (-1, 1):
            tone = (ink, shade, paper)[(j + (side == 1)) % 3]
            dy = edge * pitch
            pts = [(0, y), (side * edge, y - dy), (side * edge, y - dy + step), (0, y + step)]
            polygon(p, pts, tone)
            for i in (4, 9):
                path(p, [(0, y + i), (side * edge, y - dy + i)], mix(tone, foil, 0.42), 1.5)
    path(p, [(0, -extent), (0, extent)], foil, 2)


def groove(p: QPainter, c: Inks, density: float, label: float, extent: float) -> None:
    paper, shade, ink, foil = c
    grad = QConicalGradient(0, 0, 34)
    for t, color in [
        (0, paper),
        (0.11, shade),
        (0.21, foil),
        (0.26, ink),
        (0.4, paper),
        (0.53, shade),
        (0.68, ink),
        (0.73, foil),
        (0.84, shade),
        (1, paper),
    ]:
        grad.setColorAt(t, color)
    p.setBrush(grad)
    p.setPen(pen(ink, 2))
    p.drawEllipse(QRectF(-145, -145, 290, 290))
    for i in range(round(18 * density)):
        r = label + 8 + i * (134 - label) / (18 * density)
        oval(p, 0, 0, r, r, None, shade, 0.95)
    oval(p, 0, 0, label, label, ink, paper, 2)
    p.setPen(NONE)
    p.setBrush(foil)
    p.drawPie(QRectF(-label + 4, -label + 4, 2 * label - 8, 2 * label - 8), 16 * 32, 16 * 133)
    oval(p, 0, 0, 5, 5, paper, shade)
    p.setBrush(CLEAR)


INDUSTRIAL = {
    "chip": chip,
    "scope": scope,
    "pianola": pianola,
    "edition": edition,
    "ampersand": ampersand,
    "velodrome": velodrome,
    "spire": spire,
    "marquetry": marquetry,
    "groove": groove,
}

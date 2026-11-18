"""Symmetric line engravings, heraldic charges and ceremonial objects."""

import math

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from .drawing import Inks, contour, mix, oval, path, polygon, star


def bookplate(p: QPainter, c: Inks, density: float, width: float, extent: float) -> None:
    paper, shade, ink, foil = c
    # Four corner flourishes meet at an open oval bookplate.
    for sx in (-1, 1):
        for sy in (-1, 1):
            p.save()
            p.scale(sx, sy)
            contour(
                p,
                (9, 176),
                [
                    (83, 210, 139, 162, 128, 100),
                    (126, 70, 145, 49, 155, 56),
                    (139, 40, 108, 60, 117, 106),
                    (130, 167, 72, 178, 9, 176),
                ],
                shade,
                ink,
            )
            contour(
                p,
                (2, 178),
                [(42, 206, 85, 213, 83, 187), (82, 176, 67, 174, 68, 186)],
                edge=foil,
                width=2,
            )
            for j in range(round(5 * density)):
                p.save()
                p.translate(122 - j * 5, 122 + j * 9)
                p.rotate(14 * j - 30)
                contour(p, (0, 0), [(1, -12, 10, -25, 22, -29), (27, -15, 13, 0, 0, 0)], ink)
                p.restore()
            p.restore()
    oval(p, 0, 0, width + 15, 139, shade, ink, 2)
    oval(p, 0, 0, width + 9, 132, paper, foil, 1.5)
    oval(p, 0, 0, width - 2, 120, None, ink, 2)
    for y in (-150, 150):
        polygon(p, star(0, y, 13, 5, 4), foil)
    for side in (-1, 1):
        p.save()
        p.scale(side, 1)
        contour(
            p,
            (0, 29),
            [
                (7, 9, 12, -15, 30, -18),
                (47, -20, 50, 4, 35, 9),
                (42, -10, 22, -6, 18, 5),
                (12, 16, 11, 24, 9, 29),
            ],
            ink,
        )
        p.restore()
    contour(p, (0, -55), [(-18, -35, -13, -18, 0, 14), (13, -18, 18, -35, 0, -55)], foil)
    path(p, [(-13, 24), (13, 24)], foil, 3)
    polygon(p, star(0, 49, 12, 4, 4), ink)


def hart(p: QPainter, c: Inks, density: float, spread: float, extent: float) -> None:
    paper, shade, ink, foil = c
    polygon(p, [(-99, -76), (0, -92), (99, -76), (85, 63), (0, 143), (-85, 63)], shade, ink)
    for sign in (-1, 1):
        p.save()
        p.scale(sign, 1)
        p.save()
        p.scale(spread, 1)
        contour(p, (19, -27), [(74, -64, 108, -110, 101, -173)], edge=foil, width=6)
        for j in range(round(3 * density) + 1):
            t = (j + 1) / (round(3 * density) + 2)
            u = 1 - t
            x = 19 * u**3 + 3 * 74 * u * u * t + 3 * 108 * u * t * t + 101 * t**3
            y = -27 * u**3 - 3 * 64 * u * u * t - 3 * 110 * u * t * t - 173 * t**3
            contour(
                p, (x, y), [(x - 2, y - 12, x - 15, y - 25, x - 10, y - 46)], edge=foil, width=3.5
            )
        p.restore()
        contour(p, (16, -17), [(34, -39, 50, -45, 66, -40), (57, -17, 40, -8, 18, -4)], ink, foil)
        p.restore()
    polygon(
        p,
        [
            (-22, -30),
            (0, -24),
            (22, -30),
            (30, -3),
            (22, 29),
            (11, 62),
            (0, 75),
            (-11, 62),
            (-22, 29),
            (-30, -3),
        ],
        ink,
        foil,
    )
    polygon(p, [(0, -24), (22, -30), (27, -3), (9, 24), (0, 67)], mix(ink, paper, 0.27))
    polygon(p, [(-9, 58), (9, 58), (5, 68), (-5, 68)], paper)
    for sign in (-1, 1):
        path(p, [(sign * 22, 3), (sign * 13, 8)], paper, 3)


def keep(p: QPainter, c: Inks, density: float, height: float, extent: float) -> None:
    paper, shade, ink, foil = c
    contour(
        p,
        (-104, -126),
        [(104, -126), (103, 15), (95, 89, 48, 126, 0, 152), (-48, 126, -95, 89, -103, 15)],
        shade,
        foil,
        3,
    )
    polygon(p, [(-98, 48), (98, -95), (98, -60), (-86, 77)], mix(ink, shade, 0.55))
    for x, h in [(-47, height * 0.65), (47, height * 0.65), (0, height)]:
        contour(
            p,
            (x - 21, 47),
            [
                (x - 17, -h + 17),
                (x - 23, -h + 12),
                (x - 23, -h - 5),
                (x - 13, -h - 5),
                (x - 13, -h + 3),
                (x - 4, -h + 3),
                (x - 4, -h - 5),
                (x + 4, -h - 5),
                (x + 4, -h + 3),
                (x + 13, -h + 3),
                (x + 13, -h - 5),
                (x + 23, -h - 5),
                (x + 23, -h + 12),
                (x + 17, -h + 17),
                (x + 21, 47),
            ],
            ink,
            paper,
            2,
        )
        path(p, [(x - 18, -h + 18), (x + 18, -h + 18)], foil, 2)
        for j in range(round(2 * density)):
            p.fillRect(QRectF(x - 2, -h + 26 + j * 14, 4, 9), paper)
    contour(p, (-12, 48), [(-12, 28), (-12, 13, 12, 13, 12, 28), (12, 48)], paper)
    path(p, [(-66, 50), (66, 50)], foil, 3)
    polygon(p, star(0, 97, 15, 6, 5), foil)


def scarab(p: QPainter, c: Inks, density: float, sweep: float, extent: float) -> None:
    paper, shade, ink, foil = c
    oval(p, 0, -75, 24, 24, foil)
    oval(p, 0, -75, 30, 30, None, ink)
    for side in (-1, 1):
        p.save()
        p.scale(side, 1)
        # Each primary feather connects its own tip to a common wing joint.
        count = round(8 * density)
        for j in range(count):
            t = j / count
            tip = (149 - 29 * t, (-110 + 153 * t) * sweep)
            contour(
                p,
                (23, -7 + j * 1.7),
                [
                    (65, -16, 109, tip[1] + 28, *tip),
                    (tip[0] + 1, tip[1] + 24, 89, 55 + 20 * t, 24, 21 + j),
                ],
                mix(ink, shade, t * 0.78),
                paper,
                2,
            )
        for y in (-17, 6, 29):
            path(p, [(20, y), (41, y + 14), (49, y + 4)], foil, 2.8)
        p.restore()
    oval(p, 0, 2, 27, 45, paper, foil, 2)
    contour(p, (-22, 1), [(-23, -27, 23, -27, 22, 1), (12, 8, -12, 8, -22, 1)], ink)
    for side in (-1, 1):
        p.save()
        p.scale(side, 1)
        contour(p, (2, 7), [(20, 3, 26, 5, 22, 22), (18, 38, 9, 43, 2, 44)], ink, foil)
        p.restore()
    path(p, [(0, 52), (0, 84)], ink, 2)
    polygon(p, star(0, 91, 10, 4, 4), foil)


def kohl(p: QPainter, c: Inks, density: float, iris: float, extent: float) -> None:
    paper, shade, ink, foil = c
    for side in (-1, 1):
        for j in range(round(3 * density)):
            y = side * (119 + j * 9)
            path(p, [(-116, y), (-47, y), (0, y + side * 13), (47, y), (116, y)], shade, 2)
    contour(
        p,
        (-138, -17),
        [
            (-68, -88, 13, -77, 64, -52),
            (90, -40, 120, -40, 139, -52),
            (110, -24, 84, -26, 53, -37),
            (-8, -66, -80, -53, -138, -17),
        ],
        ink,
    )
    contour(
        p,
        (-118, 4),
        [
            (-61, -46, -1, -44, 56, -7),
            (80, 7, 104, 6, 137, -4),
            (109, 20, 73, 18, 51, 10),
            (-13, 46, -69, 42, -118, 4),
        ],
        foil,
    )
    oval(p, -19, 0, iris, iris, ink, paper, 3)
    oval(p, -19, 0, iris * 0.49, iris * 0.49, paper)
    oval(p, -26, -9, 5, 5, foil)
    contour(
        p,
        (45, 16),
        [
            (70, 49, 50, 92, 22, 86),
            (5, 83, 8, 61, 21, 62),
            (6, 78, 32, 83, 40, 64),
            (50, 45, 41, 29, 32, 22),
        ],
        ink,
    )
    polygon(p, [(-46, 31), (-34, 33), (-60, 85), (-72, 90)], ink)


def waystone(p: QPainter, c: Inks, density: float, arm: float, extent: float) -> None:
    paper, shade, ink, foil = c
    polygon(p, [(-48, -159), (9, -182), (50, -144), (57, 130), (30, 166), (-50, 151)], shade, ink)
    polygon(p, [(9, -182), (50, -144), (57, 130), (32, 147), (29, -136)], mix(shade, ink, 0.4))
    path(p, [(-37, -142), (-13, -155), (22, -134)], foil, 2)
    for j in range(3):
        y = -84 + j * 73
        path(p, [(0, y - 23), (0, y + 23)], foil, 5)
        if j == 0:
            path(p, [(-arm, y), (0, y - 20), (arm * 0.6, y - 4)], foil, 4)
        elif j == 1:
            path(p, [(0, y - 20), (arm, y - 3), (0, y + 14), (-arm * 0.72, y)], foil, 4)
        else:
            path(p, [(-arm, y - 17), (0, y + 3), (arm, y - 14)], foil, 4)
    for side in (-1, 1):
        for j in range(round(6 * density)):
            y = -96 + j * 32 / density
            polygon(
                p, [(side * 76, y - 4), (side * 81, y), (side * 76, y + 4), (side * 71, y)], ink
            )


def rangoli(p: QPainter, c: Inks, density: float, petals: float, extent: float) -> None:
    paper, shade, ink, foil = c
    count = round(petals)
    for k in range(count):
        p.save()
        p.rotate(k * 360 / count)
        contour(
            p,
            (0, -54),
            [(-32, -81, -23, -118, 0, -155), (23, -118, 32, -81, 0, -54)],
            shade,
            ink,
            2.5,
        )
        contour(p, (0, -81), [(-17, -98, -11, -119, 0, -135), (11, -119, 17, -98, 0, -81)], ink)
        for j in range(round(3 * density)):
            oval(p, 0, -145 - j * 7, 1.8, 1.8, foil)
        p.rotate(180 / count)
        contour(
            p, (0, -21), [(-26, -51, -19, -75, 0, -101), (19, -75, 26, -51, 0, -21)], paper, foil, 2
        )
        polygon(p, [(0, -89), (6, -67), (0, -54), (-6, -67)], ink)
        p.restore()
    oval(p, 0, 0, 37, 37, shade, ink, 2)
    polygon(p, star(0, 0, 31, 14, count), foil)
    oval(p, 0, 0, 9, 9, paper)


def lunation(p: QPainter, c: Inks, density: float, phase: float, extent: float) -> None:
    paper, shade, ink, foil = c
    for j in range(-2, 3):
        y = j * 74
        r = 32 - 4 * abs(j)
        oval(p, 0, y, r, r, shade, ink, 1.5)
        bow = (j / 2 + phase - 0.5) * r * 1.4
        contour(
            p,
            (0, y - r),
            [(r * 1.34, y - r, r * 1.34, y + r, 0, y + r), (bow, y + r, bow, y - r, 0, y - r)],
            foil,
        )
        for side in (-1, 1):
            path(p, [(side * (r + 8), y), (side * 109, y)], shade, 1.5)
            for i in range(round(4 * density)):
                x = side * (69 + i * 8)
                path(p, [(x, y - 2), (x, y + 2 + (i % 3 == 0) * 4)], ink, 1.2)


def hourglass(p: QPainter, c: Inks, density: float, sand: float, extent: float) -> None:
    paper, shade, ink, foil = c
    for y in (-129, 129):
        oval(p, 0, y, 77, 12, ink, foil, 2)
    for side in (-1, 1):
        path(p, [(side * 68, -123), (side * 68, 122)], ink, 5)
    for sign in (-1, 1):
        p.save()
        p.scale(1, sign)
        contour(
            p,
            (-53, -119),
            [
                (53, -119),
                (57, -74, 42, -37, 5, -8),
                (3, -3),
                (-3, -3),
                (-5, -8),
                (-42, -37, -57, -74, -53, -119),
            ],
            shade,
            foil,
            2,
        )
        p.restore()
    y = -26 - 82 * sand
    w = 20 + 28 * sand
    contour(
        p,
        (-w, y),
        [(w, y), (w - 3, y + 17, 19, -29, 0, -11), (-19, -29, -w + 3, y + 17, -w, y)],
        ink,
    )
    polygon(p, [(-48, 113), (0, 67 + 38 * sand), (48, 113)], ink)
    path(p, [(0, -10), (0, 63 + 38 * sand)], foil, 1.6)
    for j in range(round(5 * density)):
        y = -92 + j * 38 / density
        path(p, [(69, y), (79, y)], foil, 2)


def water_bearer(p: QPainter, c: Inks, density: float, tilt: float, extent: float) -> None:
    paper, shade, ink, foil = c
    # The handle, foot and incised shoulder turn with the amphora. Water
    # leaves its transformed lip in world coordinates and falls under gravity.
    p.save()
    p.translate(-39, -48)
    p.rotate(tilt)
    for side in (-1, 1):
        p.save()
        p.scale(side, 1)
        contour(p, (16, -57), [(54, -69, 70, -40, 42, -5)], edge=ink, width=7)
        p.restore()
    contour(
        p,
        (-24, -82),
        [
            (24, -82),
            (22, -72),
            (14, -69),
            (15, -50),
            (18, -32, 54, -30, 49, 4),
            (46, 28, 17, 46, 10, 61),
            (8, 69),
            (19, 74),
            (19, 80),
            (-19, 80),
            (-19, 74),
            (-8, 69),
            (-10, 61),
            (-17, 46, -46, 28, -49, 4),
            (-54, -30, -18, -32, -15, -50),
            (-14, -69),
            (-22, -72),
        ],
        ink,
        foil,
        2,
    )
    for y, w in [(-68, 14), (-43, 16), (-37, 22), (6, 46), (12, 43), (50, 15)]:
        contour(p, (-w, y), [(-w * 0.3, y + 5, w * 0.3, y + 5, w, y)], edge=paper, width=2.3)
    for k in range(3):
        polygon(p, star(k * 18 - 18, -13, 7, 2, 4), paper)
    p.restore()
    # Local-to-parent coordinates exclude the outer recipe's own transform.
    angle = math.radians(tilt)
    x = -39 + 22 * math.cos(angle) + 80 * math.sin(angle)
    y = -48 + 22 * math.sin(angle) - 80 * math.cos(angle)
    n = round(5 * density)
    for k in range(n):
        contour(
            p,
            (x, y),
            [
                (x + 60 + k * 3, y + 17, x + 43 + k * 5, 33, x + 16 + k * 5, 74),
                (x - 8 + k * 6, 110, x - 19 + k * 8, 122, x - 23 + k * 7, 151 - k * 5),
            ],
            edge=mix(ink, foil, k / n),
            width=2.4,
        )
    for j in range(7):
        px, py = -106 + j * 28, 153 + 17 * math.sin(j * 0.88)
        if j:
            path(p, [(px - 28, 153 + 17 * math.sin((j - 1) * 0.88)), (px, py)], shade, 2)
        polygon(p, star(px, py, 5 + j % 3, 1.8, 4), foil)


def signet(p: QPainter, c: Inks, density: float, diameter: float, extent: float) -> None:
    paper, shade, ink, foil = c
    # Braided cord crosses the letter under an irregular pool of sealing wax.
    for angle in (-31, 31):
        p.save()
        p.rotate(angle)
        path(p, [(0, -extent), (0, extent)], ink, 9)
        for y in range(-math.ceil(extent), math.ceil(extent), round(10 / density)):
            path(p, [(-3, y), (3, y + 5)], paper, 2)
        p.restore()
    r = diameter / 2
    rim = []
    for j in range(121):
        a = math.tau * j / 120
        rr = r * (1 + 0.05 * math.sin(3 * a) + 0.045 * math.cos(7 * a) + 0.02 * math.sin(11 * a))
        rim.append((math.cos(a) * rr, math.sin(a) * rr))
    polygon(p, rim, ink, foil)
    oval(p, 0, 0, r * 0.79, r * 0.79, shade, paper, 2.5)
    oval(p, 0, 0, r * 0.69, r * 0.69, None, ink, 1.5)
    p.rotate(-22)
    path(p, [(0, -r * 0.49), (0, r * 0.51)], foil, 2.7)
    for k in range(3):
        y = r * (0.26 - k * 0.22)
        for side in (-1, 1):
            contour(
                p,
                (0, y),
                [
                    (
                        side * r * 0.14,
                        y,
                        side * r * 0.26,
                        y - r * 0.09,
                        side * r * 0.31,
                        y - r * 0.22,
                    ),
                    (side * r * 0.15, y - r * 0.24, side * r * 0.05, y - r * 0.14, 0, y),
                ],
                ink,
            )


INSIGNIA = {
    "exlibris": bookplate,
    "hart": hart,
    "keep": keep,
    "scarab": scarab,
    "kohl": kohl,
    "stela": waystone,
    "rangoli": rangoli,
    "lunation": lunation,
    "hourglass": hourglass,
    "aquarius": water_bearer,
    "signet": signet,
}

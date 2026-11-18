"""Elemental illustrations and botanical studies in four spot inks."""

import math

from PySide6.QtGui import QPainter

from .drawing import Inks, contour, mix, oval, path, polar, polygon, star


def brazier(p: QPainter, c: Inks, density: float, lean: float, extent: float) -> None:
    paper, shade, ink, foil = c
    oval(p, 0, -16, 108, 108, None, shade, 2)
    contour(
        p,
        (-48, 50),
        [
            (-83, 11, -56, -23, -38, -48),
            (-43, -24, -30, -15, -24, -9),
            (-48, -81, 22, -103, lean, -162),
            (lean + 53, -107, 6, -82, 25, -50),
            (36, -41, 44, -55, 47, -71),
            (78, -27, 81, 17, 46, 50),
        ],
        ink,
    )
    contour(
        p,
        (-16, 52),
        [
            (-47, 22, -20, -6, -7, -26),
            (-8, -8, 1, -10, 4, -25),
            (11, -44, 10, -51, 17, -62),
            (42, -24, 11, -10, 32, 7),
            (40, 22, 33, 43, 20, 52),
        ],
        foil,
    )
    contour(
        p,
        (-82, 52),
        [
            (82, 52),
            (77, 79, 43, 91, 11, 95),
            (11, 115),
            (44, 128),
            (-44, 128),
            (-11, 115),
            (-11, 95),
            (-43, 91, -77, 79, -82, 52),
        ],
        shade,
        ink,
        2,
    )
    path(p, [(-72, 58), (72, 58)], foil, 3)
    for k in range(round(10 * density)):
        x, y = 78 * math.cos(2.4 * k), -68 - (k * 43 % 99)
        polygon(p, star(x, y, 4 + k % 3, 1.5, 4), foil)


def breaker(p: QPainter, c: Inks, density: float, curl: float, extent: float) -> None:
    paper, shade, ink, foil = c
    oval(p, 85, -135, 32, 32, ink)
    p.save()
    p.scale(1, curl)
    contour(
        p,
        (-extent, 85),
        [
            (-160, 103, -123, 75, -91, 14),
            (-63, -44, -45, -96, 20, -95),
            (72, -94, 87, -57, 68, -21),
            (73, -60, 39, -67, 20, -40),
            (-5, -4, 15, 58, 64, 71),
            (113, 85, 139, 58, extent, 48),
            (extent, extent / 0.72),
            (-extent, extent / 0.72),
        ],
        shade,
        ink,
        2,
    )
    contour(
        p,
        (-143, 74),
        [
            (-102, 61, -87, 9, -69, -24),
            (-41, -79, -2, -116, 47, -92),
            (69, -84, 82, -65, 76, -48),
            (74, -42, 69, -44, 68, -50),
            (64, -66, 54, -73, 47, -69),
            (48, -54, 42, -49, 37, -48),
            (34, -66, 20, -59, 11, -49),
            (2, -37, -6, -36, -9, -37),
            (-4, -50, -11, -50, -22, -44),
            (-63, -24, -72, 51, -143, 74),
        ],
        foil,
    )
    for k in range(round(7 * density)):
        y = k * 12 + 9
        contour(
            p,
            (-extent, 85 + y),
            [(-134, 113 + y, -96, 74 + y, -75, 45 + y), (-45, 2 + y, -40, -12 + y, -31, -21 + y)],
            edge=mix(ink, foil, k * 0.045),
            width=2.4,
        )
    for k in range(round(7 * density)):
        x, y = 9 + k * 9, -100 - 15 * math.sin(k * 2.1)
        oval(p, x, y, 2 + k % 3, 2 + k % 3, foil)
    for j in range(3):
        y = 114 + j * 42
        contour(
            p,
            (-extent, y + 26),
            [
                (-122, y + 34, -67, y - 22, -10, y - 8),
                (34, y + 4, 62, y + 22, 105, y),
                (156, y - 25, 190, y - 19, extent, y - 8),
            ],
            edge=mix(ink, shade, 0.28 + j * 0.16),
            width=3 - j * 0.4,
        )
    p.restore()


def mistral(p: QPainter, c: Inks, density: float, bend: float, extent: float) -> None:
    paper, shade, ink, foil = c
    polygon(p, [(-118, 154), (-111, -88), (-105, -88), (-108, 154)], ink)
    oval(p, -108, -95, 7, 7, foil)
    path(p, [(-108, -85), (-70, -108), (-67, -39), (-108, -85)], ink, 2)
    count = round(6 * density)

    def hem(t: float, side: int) -> tuple[float, float]:
        sag = (bend - 0.5) * 44 * t * t
        return -69 + 214 * t, -73 + sag - 29 * math.sin(t * math.pi) + side * (34 - 24 * t)

    for j in range(count):
        a, b = j / count, (j + 1) / count
        steps = [a + (b - a) * i / 6 for i in range(7)]
        polygon(
            p,
            [*(hem(t, -1) for t in steps), *(hem(t, 1) for t in reversed(steps))],
            foil if j % 2 == 0 else ink,
            paper,
        )
    oval(p, -69, -73, 7, 34, shade, foil, 2)
    for y in (100, 116, 132):
        contour(
            p,
            (-extent, y + 10),
            [(-111, y + 27, -39, y - 24, 40, y), (83, y + 12, 121, y + 3, extent, y - 10)],
            edge=shade,
            width=2,
        )
    for k in range(round(7 * density)):
        x = -62 + (k * 53) % 86
        y = -3 + k * 8 / density
        contour(
            p,
            (x, y),
            [(x + 24, y - 13, x + 59, y - 1, x + 91, y - 14)],
            edge=mix(shade, ink, 0.3),
            width=1.6,
        )


def geode(p: QPainter, c: Inks, density: float, core: float, extent: float) -> None:
    paper, shade, ink, foil = c

    def rim(r: float) -> list[tuple[float, float]]:
        result = []
        for j in range(121):
            a = math.tau * j / 120
            radius = r * (
                1 + 0.095 * math.sin(3 * a + 0.8) + 0.05 * math.cos(5 * a) + 0.04 * math.sin(8 * a)
            )
            x, y = polar(radius, a)
            result.append((x * 0.81 + 11 * math.sin(a * 2), y * 1.26))
        return result

    n = round(12 * density)
    for j in range(n):
        r = 141 - (141 - 59 * core) * j / (n - 1)
        polygon(p, rim(r), (shade, ink, mix(ink, foil, 0.6), ink, paper)[j % 5])
    polygon(p, rim(55 * core), paper)
    for j, a in enumerate(
        (0.14, 0.37, 0.64, 1.18, 1.41, 1.69, 2.22, 2.49, 3.35, 3.57, 4.06, 5.1, 5.38)
    ):
        r = (
            52
            * core
            * (1 + 0.095 * math.sin(3 * a + 0.8) + 0.05 * math.cos(5 * a) + 0.04 * math.sin(8 * a))
        )
        x, y = polar(r, a)
        p.save()
        p.translate(x * 0.81 + 11 * math.sin(2 * a), y * 1.26)
        p.rotate(math.degrees(a) - 90 + 12 * math.sin(j * 5))
        height = (19 + 11 * math.sin(2.3 * j)) * core
        polygon(
            p, [(-7, 4), (-8, -height * 0.6), (1, -height), (7, -height * 0.67), (6, 5)], ink, paper
        )
        polygon(p, [(1, -height), (7, -height * 0.67), (6, 5), (1, 2)], foil)
        p.restore()


def clerestory(p: QPainter, c: Inks, density: float, beam: float, extent: float) -> None:
    paper, shade, ink, foil = c
    polygon(
        p, [(-extent, 63), (-34, -54), (extent, 13), (extent, extent), (-extent, extent)], shade
    )
    contour(p, (-97, -118), [(-97, -200, -21, -200, -21, -118), (-21, -44), (-97, -19)], foil)
    count = round(3 * density)
    for k in range(count):
        x = -93 + 72 * k / count
        y = -20 - (x + 97) * 0.33
        w = 64 / count
        polygon(
            p,
            [
                (x, y),
                (x + w, y - w * 0.33),
                (x + w + beam * 2 + 15, extent),
                (x + beam * 2, extent),
            ],
            mix(ink, foil, 0.17 + k * 0.07),
        )
    path(p, [(-97, -103), (-21, -128)], shade, 3.5)
    for k in range(1, count):
        x = -97 + 76 * k / count
        path(p, [(x, -181 + 11 * abs(x + 59) / 38), (x, -21 - (x + 97) * 0.33)], paper, 3)
    for k in range(3):
        path(p, [(-extent, 80 + k * 12), (-34, -36 + k * 10)], mix(shade, ink, 0.42), 1.2)


def vesper(p: QPainter, c: Inks, density: float, span: float, extent: float) -> None:
    paper, shade, ink, foil = c
    oval(p, 42, -89, 69, 69, ink, foil, 2)

    def bat(x: float, y: float, s: float, angle: float) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(angle)
        p.scale(s * span, s)
        for side in (-1, 1):
            p.save()
            p.scale(side, 1)
            contour(
                p,
                (0, -4),
                [
                    (6, -19),
                    (11, -5),
                    (27, -30, 51, -35, 78, -30),
                    (56, -24, 52, -12, 48, -2),
                    (39, -10, 29, -4, 25, 9),
                    (18, 3, 10, 7, 0, 23),
                ],
                paper if s >= 1 else mix(shade, ink, 0.68),
                foil,
                1.4,
            )
            p.restore()
        p.restore()

    bat(17, -62, 1.16, -14)
    for j in range(round(6 * density)):
        x = -69 + 61 * math.sin(j * 1.7)
        bat(x, 12 + j * 25 / density, 0.44 - 0.018 * j, 12 - j * 9)


def quill(p: QPainter, c: Inks, density: float, width: float, extent: float) -> None:
    paper, shade, ink, foil = c
    p.rotate(25)
    p.scale(width, 1)
    contour(
        p,
        (0, 141),
        [
            (-8, 68, -46, 61, -54, -13),
            (-60, -70, -38, -134, 7, -171),
            (42, -126, 57, -57, 43, -8),
            (30, 48, 8, 71, 0, 141),
        ],
        ink,
    )
    contour(
        p,
        (0, 91),
        [(0, 28, 10, -82, 7, -171), (42, -126, 57, -57, 43, -8), (30, 48, 8, 71, 0, 91)],
        shade,
    )
    for j in range(round(19 * density)):
        t = (j + 1) / (round(19 * density) + 1)
        y = -151 + 224 * t
        breadth = math.sin(math.pi * t) ** 0.68
        for side in (-1, 1):
            x = side * 42 * breadth
            contour(
                p,
                (4 * (1 - t), y + 18),
                [(x * 0.6, y - 1, x * 0.85, y - 13, x, y - 25)],
                edge=paper if side < 0 else ink,
                width=1.7 if j % 4 else 3,
            )
    contour(p, (-6, 166), [(-4, 108, 9, -51, 7, -157)], edge=foil, width=2.7)


def vine(p: QPainter, c: Inks, density: float, sway: float, extent: float) -> None:
    paper, shade, ink, foil = c
    for side in (-1, 1):
        pts = [
            (side * (82 + sway * 0.55 * math.sin(y / 52)), y)
            for y in range(-math.ceil(extent), math.ceil(extent) + 3, 3)
        ]
        path(p, pts, ink, 3.2)
        for j in range(-round(5 * density), round(5 * density) + 1):
            y = j * 58 / density
            x = side * (82 + sway * 0.55 * math.sin(y / 52))
            p.save()
            p.translate(x, y)
            p.scale(side * (-1 if j % 2 else 1), 1)
            contour(p, (0, 0), [(7, -17, 26, -22, 41, -36), (38, -12, 29, 4, 0, 0)], ink)
            path(p, [(3, -2), (32, -27)], paper, 1.4)
            path(p, [(0, 0), (-11, 10), (-17, 14)], ink, 2)
            for dx, dy in [(0, 0), (-7, 5), (1, 9)]:
                oval(p, -15 + dx, 15 + dy, 4.5, 4.5, foil, paper)
            p.restore()


def ginkgo(p: QPainter, c: Inks, density: float, fan: float, extent: float) -> None:
    paper, shade, ink, foil = c
    for x, y, rotation, scale in [(-41, 84, -42, 0.88), (10, -19, 22, 1.13), (38, 131, 53, 0.64)]:
        p.save()
        p.translate(x, y)
        p.rotate(rotation)
        p.scale(scale * fan, scale)
        contour(
            p,
            (0, 0),
            [
                (-3, -23, -43, -39, -74, -61),
                (-98, -80, -85, -94, -71, -93),
                (-68, -109, -50, -114, -38, -105),
                (-27, -122, -6, -112, 0, -86),
                (10, -112, 22, -118, 32, -107),
                (48, -117, 64, -109, 69, -96),
                (86, -99, 96, -82, 80, -68),
                (49, -45, 13, -34, 0, 0),
            ],
            ink,
            paper,
            2,
        )
        for k in range(round(11 * density)):
            t = k / max(1, round(11 * density) - 1)
            dx = -74 + 148 * t
            dy = -84 - 16 * math.sin(t * math.pi)
            contour(p, (0, -3), [(dx * 0.16, -41, dx * 0.8, -66, dx, dy)], edge=shade, width=1.6)
        contour(p, (0, 1), [(-4, 25, -13, 40, -21, 54)], edge=foil, width=2.5)
        p.restore()


NATURAL_HISTORY = {
    "brazier": brazier,
    "breaker": breaker,
    "mistral": mistral,
    "geode": geode,
    "clerestory": clerestory,
    "vesper": vesper,
    "quill": quill,
    "vine": vine,
    "ginkgo": ginkgo,
}

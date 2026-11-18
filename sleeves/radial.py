"""Central medallions and instruments, constructed around the optical centre."""

import math

from PySide6.QtGui import QPainter

from .drawing import Inks, mix, oval, path, polar, polygon, star

TAU = math.tau


def radial_plate(p: QPainter, key: str, c: Inks, density: float, v: float, extent: float) -> None:
    paper, shade, ink, foil = c
    n = round(24 * density)
    if key == "sun":
        for k in range(n):
            a = TAU * k / n
            polygon(
                p,
                [
                    polar(v * 0.92, a),
                    polar(extent * 2, a + 0.07),
                    polar(extent * 2, a + TAU / n * 0.73),
                ],
                shade,
            )
            path(p, [polar(v + 10, a), polar(extent * 2, a + 0.07)], ink, 1.1)
        oval(p, 0, 0, v, v, ink, foil, 2)
        oval(p, 0, 0, v * 0.87, v * 0.87, None, paper, 1)
        for k in range(1, 8):
            y = v * k / 10
            w = math.sqrt(v * v * 0.74 - y * y)
            path(p, [(-w, y), (w, y)], mix(ink, paper, 0.6), 1 + k / 5)
    elif key == "orbit":
        for r in range(28, int(extent * 1.6), round(26 / density)):
            oval(p, 0, 0, r, r, None, shade)
        for k in range(round(5 * density) + 1):
            p.save()
            p.rotate(v * k - 23)
            r = 47 + k * 20
            oval(p, 0, 0, r, r * 0.46, None, ink, 1.4)
            a = k * 2.4
            oval(
                p, r * math.cos(a), r * 0.46 * math.sin(a), 4 + k * 0.55, 4 + k * 0.55, foil, paper
            )
            p.restore()
        oval(p, 0, 0, 12, 12, foil)
    elif key == "nautilus":
        for k in range(n):
            a = TAU * k / n
            curve = [
                polar(extent * 1.5 * (i / 90) ** 2, a + v * math.log1p(i / 7)) for i in range(91)
            ]
            other = [
                polar(extent * 1.5 * (i / 90) ** 2, a + v * math.log1p(i / 7) + TAU / n * 0.65)
                for i in range(90, -1, -1)
            ]
            polygon(p, [*curve, *other], mix(shade, ink, 0.28 if k % 2 else 0.05))
            path(p, curve, ink, 1.35)
    elif key == "rose":
        for r in (134, 139, 148):
            oval(p, 0, 0, r, r, paper if r == 134 else None, ink if r == 139 else shade, 2)
        rings = round(3 * density) + 1
        for ring in range(rings, 0, -1):
            r = 133 * ring / rings
            for k in range(round(v)):
                p.save()
                p.rotate(k * 360 / round(v) + (ring % 2) * 180 / round(v))
                pts = [
                    polar(r * (0.52 + 0.48 * math.cos(t)), 0.9 * math.sin(t) / round(v) * math.pi)
                    for t in (TAU * j / 64 for j in range(65))
                ]
                polygon(p, pts, mix(shade, ink, 0.29 if ring % 2 else 0.08), ink)
                path(p, [(r * 0.21, 0), (r * 0.89, 0)], mix(ink, foil, 0.45), 1.2)
                p.restore()
        oval(p, 0, 0, 11, 11, foil, ink, 2)
    elif key == "eclipse":
        for k in range(n * 3):
            a = TAU * k / (n * 3)
            path(p, [polar(94, a), polar(99 + (k % 6 == 0) * 12, a)], ink, 1)
        oval(p, 0, 0, 81, 81, ink, foil, 2)
        oval(p, 81 * v, -9, 75, 75, paper, shade)
        for side in (-1, 1):
            path(p, [(0, side * 115), (0, side * 202)], shade, 2)
            for j in range(4):
                oval(p, 0, side * (130 + j * 20), 6 - j, 6 - j, ink if j % 2 else paper, ink)
    elif key == "guilloche":
        for k in range(round(41 * density)):
            pts = [
                polar(102 + 38 * math.cos(round(v) * a + k * 0.1) + k * 0.82, a)
                for a in (TAU * j / 420 for j in range(421))
            ]
            path(p, pts, mix(ink, foil, k / (42 * density)), 0.8)
    elif key == "compass":
        for r in (79, 83, 121, 126, 139):
            oval(p, 0, 0, r, r, None, ink if r in (83, 126) else shade, 1.5)
        for k in range(n * 3):
            a = TAU * k / (n * 3)
            path(p, [polar(126, a), polar(131 + 4 * (k % 3 == 0), a)], ink)
        points = round(v)
        for k in range(points):
            a = TAU * k / points
            tip = polar(141 if k % 2 == 0 else 99, a)
            polygon(p, [(0, 0), polar(32, a - math.pi / points), tip], ink)
            polygon(p, [(0, 0), polar(32, a + math.pi / points), tip], shade, ink)
        polygon(p, star(0, 0, 11, 7, 8), foil)
    else:
        for x, y in [(-v, -62), (v, 62)]:
            for r in range(7, int(extent * 2.3), round(13 / density)):
                oval(p, x, y, r, r, None, shade if x < 0 else ink, 2.4 if x < 0 else 1.3)

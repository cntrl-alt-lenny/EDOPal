"""Repeat blocks for the first eight plates, drawn in local tile coordinates."""

import math

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

from .drawing import Inks, mix, oval, path, polar, polygon


def repeat_plate(
    p: QPainter, key: str, c: Inks, density: float, detail: float, extent: float
) -> None:
    paper, shade, ink, foil = c
    span = 54 / density
    height = {
        "fans": 0.66,
        "scales": 0.5,
        "stars": 0.866,
        "tiles": 0.866,
        "sprigs": 1.15,
        "arches": 1.25,
        "quilt": 1.4,
    }.get(key, 1)
    if key == "weave":
        p.rotate(-34)
    nx, ny = math.ceil(extent / span) + 2, math.ceil(extent / (span * height)) + 2
    for j in range(-ny, ny + 1):
        for i in range(-nx, nx + 1):
            stagger = (
                0.5
                if j % 2 and key in ("fans", "scales", "stars", "tiles", "sprigs", "arches")
                else 0
            )
            p.save()
            p.translate((i + stagger) * span, j * span * height)
            p.scale(span / 54, span / 54)
            if key == "fans":
                vertices = [polar(34, math.pi * (1.1 + 0.8 * k / 32)) for k in range(33)]
                polygon(p, [(0, 0), *vertices], shade, ink)
                for k in range(1, round(detail)):
                    path(
                        p, [(0, 0), polar(31, math.pi * (1.1 + 0.8 * k / round(detail)))], ink, 0.8
                    )
                oval(p, 0, 0, 2, 2, foil)
            elif key == "weave":
                if (i + j) % 2:
                    p.rotate(90)
                p.fillRect(QRectF(-26, -24, 52, 48), shade)
                for k in range(round(detail)):
                    y = -21 + 42 * k / (round(detail) - 1)
                    path(p, [(-25, y), (25, y)], mix(ink, foil, k / round(detail)), 2.4)
            elif key == "stars":
                for k in range(6):
                    u, v = polar(31.18, (k + 0.5) * math.pi / 3)
                    a, b = polar(31.18, (k + 1.5) * math.pi / 3)
                    polygon(p, [(0, 0), (u, v), (a, b)], paper if k % 2 else shade, ink)
                    path(
                        p, [(u, v), ((a + u) * detail / 2, (b + v) * detail / 2), (a, b)], ink, 0.7
                    )
            elif key == "scales":
                oval(p, 0, 0, 28, 28, paper, ink, 1.6)
                for k in range(1, round(detail)):
                    r = 28 * k / round(detail)
                    oval(p, 0, 0, r, r, None, shade, 1.8)
            elif key == "tiles":
                for k, face in enumerate((shade, ink, paper)):
                    a = polar(31.18, (2 * k - 0.5) * math.pi / 3)
                    b = polar(31.18, (2 * k + 0.5) * math.pi / 3)
                    d = polar(31.18, (2 * k + 1.5) * math.pi / 3)
                    polygon(p, [(0, 0), a, b, d], face, shade)
                    path(
                        p,
                        [(x * detail, y * detail) for x, y in (a, b, d)],
                        mix(face, foil, 0.4),
                        0.9,
                    )
            elif key == "quilt":
                for x, y, base in [(0, 0, shade), (27, 37.8, paper)]:
                    pts = [(0, -37.8), (27, 0), (0, 37.8), (-27, 0)]
                    polygon(p, [(x + a, y + b) for a, b in pts], base, ink)
                    polygon(
                        p,
                        [(x + a * detail, y + b * detail) for a, b in pts],
                        mix(base, ink, 0.35),
                        foil,
                    )
            elif key == "sprigs":
                p.rotate(-24 if (i + j) % 2 else 24)
                path(p, [(0, 22), (0, -18)], ink, 1.3)
                for k in range(3):
                    for side in (-1, 1):
                        y = 16 - k * 11
                        polygon(
                            p,
                            [
                                (0, y),
                                (side * 7, y - 1),
                                (side * (15 - k) * detail, y - 14),
                                (side * 4, y - 10),
                            ],
                            ink,
                        )
                oval(p, 0, -20, 2.4, 3.5, foil)
            else:  # rounded architectural arch, with a keystone
                for k in range(round(detail)):
                    r = 24 - 12 * k / (round(detail) - 1)
                    arch = [polar(r, math.pi * (1 + n / 30)) for n in range(31)]
                    path(p, [(-r, 28), *arch, (r, 28)], ink if k == 0 else shade, 2.3)
                polygon(p, [(-3, -26), (3, -26), (2, -18), (-2, -18)], foil)
            p.restore()

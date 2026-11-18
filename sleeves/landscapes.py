"""Scenic plates with repeatable, locally seeded skies and terrain."""

import math
import random

from PySide6.QtGui import QPainter

from .drawing import Inks, mix, oval, path, polygon, star


def scene_plate(
    p: QPainter, key: str, c: Inks, density: float, v: float, extent: float, rng: random.Random
) -> None:
    paper, shade, ink, foil = c
    e = math.ceil(extent)
    xs = range(-e - 6, e + 7, 9 if p.device().height() <= 254 else 5)
    if key in ("dunes", "alpine"):
        oval(p, -55, -95, 40, 40, foil)
        oval(p, -55, -95, 45, 45, None, ink, 0.8)
        for _ in range(round(48 * density)):
            x, y = rng.uniform(-195, 195), rng.uniform(-265, -45)
            oval(p, x, y, 1, 1, ink)
        n = round(6 * density) + 2
        for k in range(n):
            horizon = -41 + k * 260 / (n - 1)
            phase = rng.random() * 6
            tone = mix(ink, shade, k / (n - 1))
            if key == "dunes":
                pts = [
                    (x, horizon + v * math.sin(x * 0.012 + phase) + 9 * math.sin(x * 0.027 + phase))
                    for x in xs
                ]
            else:
                pts = [
                    (
                        x,
                        horizon
                        - v * (0.52 + 0.43 * math.sin(0.019 * x + phase))
                        - rng.uniform(0, v * 0.21),
                    )
                    for x in range(-e - 60, e + 61, 33)
                ]
            polygon(p, [*pts, (pts[-1][0], e + 100), (pts[0][0], e + 100)], tone)
            path(p, pts, mix(tone, foil, 0.55), 1.2)
            if key == "alpine":
                for a, b, d in zip(pts, pts[1:], pts[2:], strict=False):
                    if a[1] > b[1] < d[1]:
                        polygon(
                            p,
                            [
                                b,
                                d,
                                (b[0] + 12, b[1] + 22),
                                (b[0] + 5, b[1] + 16),
                                (b[0] - 4, b[1] + 27),
                            ],
                            mix(tone, paper, 0.30),
                        )
            else:
                for j in (4, 9, 15):
                    path(
                        p,
                        [(x, y + j + 0.05 * j * math.sin(0.02 * x) * j) for x, y in pts],
                        mix(tone, paper, 0.18),
                        0.9,
                    )
    elif key == "rain":
        p.rotate(28)
        for _ in range(round(165 * density)):
            x, y = rng.uniform(-e, e), rng.uniform(-e, e)
            s = rng.uniform(0.35, 1)
            polygon(
                p,
                [(x, y - v * s), (x + s * 2.2, y), (x, y + 2), (x - s * 2.2, y)],
                mix(ink, foil, s * 0.62),
            )
            if s > 0.88:
                polygon(p, star(x, y, 4, 1.3, 4), foil)
    elif key == "sky":
        points = [(rng.uniform(-e, e), rng.uniform(-e, e)) for _ in range(round(220 * density))]
        for i, (x, y) in enumerate(points):
            near = sorted((math.hypot(x - u, y - w), u, w) for u, w in points[i + 1 :])
            for d, u, w in near[:2]:
                if 12 < d < v:
                    path(p, [(x, y), (u, w)], mix(shade, ink, 0.59), 1.2)
        for i, (x, y) in enumerate(points):
            if i % 9 == 0:
                polygon(p, star(x, y, 5, 1.5, 4), foil)
                oval(p, x, y, 8, 8, None, shade)
            else:
                r = rng.uniform(0.65, 2.1)
                oval(p, x, y, r, r, ink)
    elif key == "tide":
        for y in range(-e - 85, e + 85, round(7 / density)):
            pts = [
                (
                    x,
                    y
                    + v * math.sin(x * 0.016 + y * 0.009)
                    + 0.32 * v * math.sin(x * 0.031 - y * 0.017),
                )
                for x in xs
            ]
            path(p, pts, mix(shade, foil, 0.42 + 0.4 * math.sin(y * 0.041)), 1.6)
    elif key == "ribbon":
        for x in range(-e - 60, e + 60, round(69 / density)):
            for j in range(8):
                pts = [
                    (x + 32 * math.sin(y * 0.018) + v * math.cos(y * 0.018) * j / 7, y) for y in xs
                ]
                path(p, pts, mix(shade, foil, j / 8), 1.55)
    elif key == "light":
        for _ in range(round(225 * density)):
            x, y = rng.uniform(-e, e), rng.uniform(-e, e)
            oval(p, x, y, 1, 1, ink)
        for k in range(3):
            pts = [
                (
                    x,
                    -100
                    + k * 95
                    + v * (0.48 * math.sin(x * 0.022 + k) + 0.52 * math.sin(x * 0.009 + k)),
                )
                for x in xs
            ]
            for band in range(12, 0, -1):
                upper = [
                    (
                        x - 6 * band / 12,
                        y - (15 + 35 * (1 + math.sin(x * 0.018 + k))) * density * band / 12,
                    )
                    for x, y in reversed(pts)
                ]
                polygon(p, [*pts, *upper], mix(paper, ink, (1 - band / 13) ** 1.2 * 0.76))
            path(p, pts, ink, 2)
    else:
        n = round(v)
        for k in range(round(11 * density), 0, -1):
            radius = k * 22 / density
            pts = []
            for j in range(n + 1):
                x, y = radius * j / n, -radius * (1 - j / n)
                pts.extend([(x, y), (x + radius / n, y)])
            for side in (-1, 1):
                for top in (-1, 1):
                    path(
                        p, [(x * side, y * top) for x, y in pts], ink if k % 3 == 0 else shade, 2.4
                    )
        polygon(p, star(0, 0, 12, 5, 4), foil)

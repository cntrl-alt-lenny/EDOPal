"""Render the four offline sleeve proof sheets. Run from checkout with --out DIR."""

import argparse
import math
import random
import sys
from pathlib import Path
from zipfile import ZipFile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sleeves import (  # noqa: E402
    EMBLEMS,
    PALETTES,
    PATTERNS,
    Emblem,
    Layout,
    Recipe,
    compose,
    ensure_gui,
    png_bytes,
    render,
)
from sleeves.drawing import mix, oval, path, polygon  # noqa: E402


def stand_in() -> QImage:
    """An original square landscape with fine detail and off-centre landmarks for crop review."""
    img = QImage(620, 620, QImage.Format.Format_RGB888)
    rng = random.Random(174)
    navy, sea, salmon, sand = map(QColor, ("#192d42", "#466978", "#ea997c", "#ffdea7"))
    with QPainter(img) as p:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sky = QLinearGradient(0, 0, 0, 375)
        sky.setColorAt(0, navy)
        sky.setColorAt(0.53, salmon)
        sky.setColorAt(1, sand)
        p.fillRect(img.rect(), sky)
        oval(p, 400, 172, 64, 64, sand)
        for k in range(4):
            pts = [
                (
                    x,
                    286
                    + 27 * k
                    - 60 * math.sin(0.012 * x + k)
                    - 28 * math.sin(0.021 * x + 2 * k)
                    - rng.uniform(0, 10),
                )
                for x in range(-4, 633, 13)
            ]
            polygon(p, [*pts, (634, 501), (-4, 501)], mix(sea, navy, k / 3))
        water = QLinearGradient(0, 365, 0, 620)
        water.setColorAt(0, sea)
        water.setColorAt(1, navy)
        p.fillRect(QRectF(0, 365, 620, 255), water)
        for _ in range(410):
            x, y = rng.uniform(0, 620), rng.uniform(367, 620)
            path(
                p,
                [(x, y), (x + rng.uniform(2, 23), y)],
                mix(sea, sand, 0.48 if abs(x - 400) < (y - 310) / 2 else 0.09),
                rng.uniform(0.7, 2.2),
            )
        for x, y, s in [
            (76, 445, 1),
            (117, 426, 0.62),
            (27, 450, 1.45),
            (583, 402, 0.68),
            (609, 406, 0.83),
        ]:
            for z in range(7):
                a = (z + 1) * 4 * s
                h = y - (105 - z * 12) * s
                polygon(
                    p,
                    [(x, h - 26 * s), (x + a, h + 4 * s), (x + 3 * s, h), (x - a, h + 7 * s)],
                    navy,
                )
            p.fillRect(QRectF(x - 2 * s, y - 30 * s, 4 * s, 35 * s), navy)
        for x, y in [(230, 117), (246, 110), (260, 125)]:
            path(p, [(x - 6, y - 2), (x, y + 2), (x + 7, y - 3)], navy, 1.6)
    return img


def sheet(
    out: Path, title: str, subtitle: str, cells: list[tuple[str, str, QImage]], cols: int
) -> None:
    cell_w, cell_h = 196, 273
    w = 48 + cell_w * cols
    h = 106 + math.ceil(len(cells) / cols) * cell_h + 18
    image = QImage(w, h, QImage.Format.Format_RGB888)
    image.fill(QColor("#f2efe8"))
    with QPainter(image) as p:
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )

        def label(x: int, y: int, value: str, size: int, bold: bool = False) -> None:
            font = QFont(QGuiApplication.font())
            font.setPixelSize(size)
            font.setBold(bold)
            p.setFont(font)
            p.setPen(QColor("#242e33" if size != 12 else "#536267"))
            p.drawText(x, y, value)

        label(35, 46, title, 34)
        label(37, 72, subtitle, 15)
        path(p, [(36, 88), (w - 36, 88)], QColor("#bdc1b8"))
        for i, (name, sub, img) in enumerate(cells):
            x, y = 24 + i % cols * cell_w, 106 + i // cols * cell_h
            available = QRectF(x + 8, y, cell_w - 16, 210)
            factor = min(available.width() / img.width(), available.height() / img.height())
            dst = QRectF(0, 0, img.width() * factor, img.height() * factor)
            dst.moveCenter(available.center())
            p.fillRect(dst.translated(2, 3), QColor("#d4d1c8"))
            thumb = img.scaled(
                dst.size().toSize(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawImage(dst, thumb)
            label(round(dst.x()), y + 233, name, 15, True)
            label(round(dst.x()), y + 251, sub, 12)
    if not image.save(str(out), "PNG"):
        raise OSError(f"Could not save {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    ensure_gui()
    from extras import PACKS

    with ZipFile(PACKS / "shared.zip") as archive:
        font_id = QFontDatabase.addApplicationFontFromData(archive.read("fonts/Inter-Medium.ttf"))
    QGuiApplication.setFont(QFont(QFontDatabase.applicationFontFamilies(font_id)[0]))
    sheet(
        out / "patterns.png",
        "Pattern library",
        "24 engraved, woven and scenic designs in their default inks.",
        [(s.name, s.palette, render(Recipe(k))) for k, s in PATTERNS.items()],
        6,
    )
    sheet(
        out / "palettes.png",
        "Colour library",
        "Sol invictus printed in each of the 16 palettes.",
        [(k, "Sol invictus", render(Recipe("sun", palette=v))) for k, v in PALETTES.items()],
        4,
    )
    variants = [
        (s, f, render(Recipe("fans", PALETTES["Peacock"], frame=f, emblem=Emblem(s, 0.36))))
        for s, f in zip(
            EMBLEMS, ("deco", "keyline", "observatory", "none", "deco", "observatory"), strict=True
        )
    ]
    sheet(
        out / "finishes.png",
        "Frames and emblems",
        "Silk fans in Peacock with six centre emblems and four edge treatments.",
        [(s.title(), f.title(), img) for s, f, img in variants],
        3,
    )
    art = stand_in()
    data = png_bytes(art)
    sheet(
        out / "card-art.png",
        "Card artwork",
        "A square illustration composed for the two sleeve slots.",
        [
            ("Original illustration", "620 × 620", art),
            ("Framed", "Colours from illustration", compose(data, Layout("framed"))),
            (
                "Framed",
                "Lacquer palette",
                compose(data, Layout("framed", palette=PALETTES["Lacquer"])),
            ),
            (
                "Full bleed",
                "Crop follows the sun",
                compose(data, Layout("fill", (190, 0, 430, 620))),
            ),
        ],
        4,
    )


if __name__ == "__main__":
    main()

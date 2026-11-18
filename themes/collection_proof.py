"""Render the complete collection and time uncached wallpaper rasterization.

Run python -m themes.collection_proof --out <directory> with QT_QPA_PLATFORM=offscreen.
"""

import argparse
from pathlib import Path
from statistics import median
from time import perf_counter

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from config import APP_NAME

from .imaging import ensure_gui
from .model import Theme
from .presets import FAMILIES, PRESETS
from .preview import label, render_preview
from .wallpaper import PROCEDURAL_STYLES, procedural_wallpaper

PAPER = "#f6f4ed"
INK = "#202a2b"
MUTED = "#526166"


def _page(w: int, h: int, title: str, subtitle: str) -> tuple[QImage, QPainter]:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(PAPER))
    p = QPainter(img)
    label(p, QRectF(24, 10, w - 48, 54), title, INK, 34, bold=True)
    label(p, QRectF(25, 65, w - 50, 30), subtitle, MUTED, 18)
    return img, p


def _write(img: QImage, out: Path) -> None:
    if not img.save(str(out), "PNG"):
        raise OSError(f"Could not save {out}")


def collection(out: Path) -> None:
    thumb = (304, 190)
    pitch = 320
    row_height = 473
    width = 48 + max(map(len, FAMILIES.values())) * pitch - 16
    img, p = _page(
        width,
        116 + row_height * len(FAMILIES),
        f"{APP_NAME}  /  Built-in looks",
        f"{len(PRESETS)} palettes     ·     Main menu above, duel field below",
    )
    try:
        for row, (family, entries) in enumerate(FAMILIES.items()):
            y = 112 + row * row_height
            p.fillRect(24, y, width - 48, 1, QColor("#b7bcb6"))
            label(p, QRectF(24, y + 5, width - 48, 34), family, MUTED, 20, bold=True)
            for i, theme in enumerate(entries):
                x = 24 + i * pitch
                label(p, QRectF(x, y + 39, 232, 32), theme.name, INK, 18, bold=True)
                for j, color in enumerate((theme.background, theme.surface, theme.accent)):
                    p.fillRect(x + 236 + j * 23, y + 48, 18, 15, QColor(color))
                for j, scene in enumerate(("menu", "duel")):
                    p.drawImage(x, y + 77 + 193 * j, render_preview(theme, scene, thumb))
    finally:
        p.end()
    _write(img, out / "preset-collection.png")


def papers(out: Path, palettes: tuple[Theme, ...]) -> None:
    thumb = (304, 190)
    row_height = 252
    width = 1936
    img, p = _page(
        width,
        108 + row_height * ((len(PROCEDURAL_STYLES) + 1) // 2),
        f"{APP_NAME}  /  Wallpaper studies",
        "Left to right in each study     ·     " + "    /    ".join(t.name for t in palettes),
    )
    try:
        for i, style in enumerate(PROCEDURAL_STYLES):
            x = 24 + (i % 2) * 955
            y = 108 + (i // 2) * row_height
            p.fillRect(x, y, 928, 1, QColor("#b7bcb6"))
            label(p, QRectF(x, y + 3, 928, 38), style.title(), INK, 22, bold=True)
            for j, theme in enumerate(palettes):
                p.drawImage(x + 312 * j, y + 47, procedural_wallpaper(theme, style, thumb))
    finally:
        p.end()
    _write(img, out / "wallpaper-studies.png")


def timings(palettes: tuple[Theme, ...]) -> None:
    print("1920x1080 uncached QImage raster, median of nine frames (three palettes x three seeds)")
    for style in PROCEDURAL_STYLES:
        samples = []
        for seed in (0, 1, 2):
            for theme in palettes:
                start = perf_counter()
                procedural_wallpaper(theme, style, (1920, 1080), seed=seed)
                samples.append((perf_counter() - start) * 1000)
        print(f"{style:12} {median(samples):5.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    ensure_gui()
    by_name = {theme.name: theme for theme in PRESETS}
    palettes = tuple(by_name[n] for n in ("Deep Current", "Saffron Wash", "Cherry After Rain"))
    collection(out)
    papers(out, palettes)
    timings(palettes)


if __name__ == "__main__":
    main()

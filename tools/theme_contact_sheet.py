"""Render labelled preset proofs. Run with QT_QPA_PLATFORM=offscreen."""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from themes import PRESETS, SCENES, STYLES, WIDGETS, Theme, ensure_gui, render_preview, wallpaper
from themes.preview import label
from themes.widgets import draw_widget

PAPER = "#f7f6f1"
INK = "#232b32"
MUTED = "#4b5a62"
CONTROL_LABELS = (
    "Button",
    "Pressed",
    "Disabled",
    "Menu bar",
    "Menu pane",
    "Menu pressed",
    "Checkbox",
    "Disabled check",
    "Combo box",
    "Disabled combo",
    "Sunken pane",
    "Tab control",
    "Tab button",
    "Active tab",
    "Window",
)


def sheet(w: int, h: int) -> tuple[QImage, QPainter]:
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(QColor(PAPER))
    painter = QPainter(image)
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
    )
    return image, painter


def save(image: QImage, out: Path) -> None:
    if not image.save(str(out), "PNG"):
        raise OSError(f"Could not save {out}")


def preset_sheet(theme: Theme, out: Path) -> None:
    image, p = sheet(1824, 658)
    label(p, QRectF(28, 14, 840, 57), theme.name, INK, 40, bold=True)
    label(
        p,
        QRectF(30, 72, 650, 28),
        f"{theme.wallpaper.title()} wallpaper    ·    Corner radius {theme.roundness}    ·    Depth {theme.depth}%",
        MUTED,
        17,
    )
    for i, (name, color) in enumerate(
        (("Background", theme.background), ("Surface", theme.surface), ("Accent", theme.accent))
    ):
        x = 1202 + i * 200
        p.fillRect(x, 35, 38, 38, QColor(color))
        label(p, QRectF(x + 49, 28, 145, 24), name, MUTED, 15)
        label(p, QRectF(x + 49, 51, 145, 25), color.upper(), INK, 18, bold=True)
    p.fillRect(30, 115, 1764, 1, QColor("#c4cac6"))
    for i, (scene, title) in enumerate(
        zip(SCENES, ("Main menu", "Deck editor", "Duel field"), strict=True)
    ):
        x = 30 + i * 594
        label(p, QRectF(x, 126, 530, 31), title, INK, 19, bold=True)
        p.drawImage(x, 166, render_preview(theme, scene, (576, 360)))
    label(p, QRectF(30, 539, 620, 28), "Painted controls", INK, 18, bold=True)
    for i, (name, title) in enumerate(zip(WIDGETS, CONTROL_LABELS, strict=True)):
        x = 30 + i * 118
        p.fillRect(x, 577, 111, 47, QColor(theme.background))
        w = 21 if name.startswith("CheckBox") else 96
        h = 21 if name.startswith("CheckBox") else (39 if name == "Window" else 31)
        draw_widget(p, theme, name, QRectF(x + (111 - w) / 2, 577 + (47 - h) / 2, w, h))
        label(p, QRectF(x - 3, 625, 117, 27), title, MUTED, 15, centered=True)
    p.end()
    save(image, out / (theme.name.lower().replace(" & ", "-").replace(" ", "-") + ".png"))


def wallpapers(out: Path) -> None:
    theme = PRESETS[0]
    image, p = sheet(1576, 1135)
    label(p, QRectF(28, 12, 1450, 64), f"{theme.name}   /   Wallpapers", INK, 38, bold=True)
    for i, style in enumerate(STYLES):
        x, y = 28 + (i % 2) * 770, 88 + (i // 2) * 522
        label(p, QRectF(x, y, 720, 34), style.title(), MUTED, 22)
        p.drawImage(x, y + 41, wallpaper(replace(theme, wallpaper=style), (752, 470)))
    p.end()
    save(image, out / "wallpaper-styles.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    out = parser.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    ensure_gui()
    for theme in PRESETS:
        preset_sheet(theme, out)
    wallpapers(out)
    print(f"Rendered {len(PRESETS)} preset sheets and one wallpaper sheet in {out}")


if __name__ == "__main__":
    main()

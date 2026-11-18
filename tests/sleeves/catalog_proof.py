"""Offline visual proofs. Run with python -m tests.sleeves.catalog_proof --out DIR."""

import argparse
from collections.abc import Iterator
from pathlib import Path
from statistics import median
from time import perf_counter

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter

from sleeves import PALETTES, PATTERNS, Recipe, ensure_gui, render

Plate = tuple[str, str, Recipe]
STUDIES = ("scarab", "breaker", "geode", "spire", "crane")


def _draw_sheet(file: Path, heading: str, cells: list[Plate], *, game: bool, family: str) -> None:
    height = 100 if game else 162
    width = round(height * 177 / 254)
    cols = 10 if game else 7
    pitch_x, pitch_y = (138, 137) if game else (161, 210)
    margin, top = 18, 61
    rows = (len(cells) + cols - 1) // cols
    sheet = QImage(
        2 * margin + cols * pitch_x, top + rows * pitch_y + 10, QImage.Format.Format_RGB32
    )
    sheet.fill(QColor("#eeede7"))
    with QPainter(sheet) as p:

        def label(x: int, y: int, name: str, size: int, heavy: bool = False) -> None:
            font = QFont(family)
            font.setPixelSize(size)
            font.setBold(heavy)
            p.setFont(font)
            p.setPen(QColor("#212b30" if heavy else "#43545a"))
            p.drawText(x, y, name)

        label(margin + 3, 36, heading, 24, True)
        for i, (name, note, recipe) in enumerate(cells):
            x, y = margin + (i % cols) * pitch_x, top + (i // cols) * pitch_y
            thumb = render(recipe).scaled(
                width,
                height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            left = x + (pitch_x - width) // 2
            p.drawImage(left, y, thumb)
            label(x + 5, y + height + 17, name, 11 if game else 13, True)
            if not game:
                label(x + 5, y + height + 33, note, 12)
    if not sheet.save(str(file), "PNG"):
        raise OSError(f"Could not save {file}")


def palette_study(key: str) -> Iterator[Plate]:
    for name, values in PALETTES.items():
        yield name, PATTERNS[key].name, Recipe(key, values)


def _matrix(file: Path, family: str) -> None:
    """Keep each ink set on one row for side-by-side comparisons."""
    rows = (len(PALETTES) + 2) // 3
    panel, pitch, row_height = 477, 66, 88
    sheet = QImage(3 * panel + 20, 100 + rows * row_height, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#eeede7"))
    with QPainter(sheet) as p:
        font = QFont(family)
        font.setPixelSize(23)
        p.setFont(font)
        p.setPen(QColor("#212b30"))
        p.drawText(16, 30, "Five plates across the ink library")
        font.setPixelSize(12)
        p.setFont(font)
        for group in range(3):
            for col, key in enumerate(STUDIES):
                x = 16 + group * panel + 128 + col * pitch
                first, _, rest = PATTERNS[key].name.partition(" ")
                p.drawText(x, 61, first)
                p.drawText(x, 76, rest)
        for i, (name, inks) in enumerate(PALETTES.items()):
            x = 16 + i // rows * panel
            y = 88 + i % rows * row_height
            p.drawText(x, y + 35, name)
            for k, ink in enumerate(inks):
                p.fillRect(x + k * 19, y + 44, 19, 8, QColor(ink))
            for col, key in enumerate(STUDIES):
                preview = render(Recipe(key, inks), thumbnail=True)
                image = preview.scaled(
                    54,
                    77,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p.drawImage(x + 128 + col * pitch, y, image)
    if not sheet.save(str(file), "PNG"):
        raise OSError(f"Could not save {file}")


def benchmark() -> None:
    # Exclude startup and PNG I/O. Measure repeat renders across different inks,
    # not repeated lookups of a cached bitmap. Report medians to limit scheduler noise.
    for name, thumbnail, scale in [
        ("Direct thumbnail", True, False),
        ("Full export", False, False),
        ("Current gallery", False, True),
    ]:
        times = {}
        for key in PATTERNS:
            samples = []
            for palette in list(PALETTES.values())[:5]:
                recipe = Recipe(key, palette)
                start = perf_counter()
                image = render(recipe, thumbnail=thumbnail)
                if scale:
                    image = image.scaled(
                        177,
                        254,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                samples.append(1000 * (perf_counter() - start))
            times[key] = median(samples)
        slow = max(times, key=times.get)
        print(
            f"{name}: median pattern {median(times.values()):.1f} ms; "
            f"slowest {PATTERNS[slow].name} {times[slow]:.1f} ms",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    ensure_gui()
    font = Path(__file__).resolve().parents[2] / "assets/window/fonts/Inter-Medium.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font))
    family, *_ = QFontDatabase.applicationFontFamilies(font_id)
    neutral = "Carbon silver"
    ordered = sorted(PATTERNS.items(), key=lambda kv: (kv[1].family, kv[1].name))
    _draw_sheet(
        args.out / "patterns-neutral.png",
        f"{len(ordered)} patterns in {neutral}",
        [(s.name, s.family, Recipe(k, PALETTES[neutral])) for k, s in ordered],
        game=False,
        family=family,
    )
    _draw_sheet(
        args.out / "in-game.png",
        "Sleeves at duel size  ·  default inks",
        [(s.name, s.palette, Recipe(k)) for k, s in ordered],
        game=True,
        family=family,
    )
    for key in ("groove", *STUDIES):
        file = "palettes.png" if key == "groove" else f"study-{key}.png"
        _draw_sheet(
            args.out / file,
            f"{PATTERNS[key].name}  ·  {len(PALETTES)} palettes",
            list(palette_study(key)),
            game=False,
            family=family,
        )
    _matrix(args.out / "five-plates.png", family)
    if args.benchmark:
        benchmark()


if __name__ == "__main__":
    main()

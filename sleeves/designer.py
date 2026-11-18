"""Pure recipe-to-pixel renderer. Uses no fonts, system randomness, or global painter state."""

import math
import random

from PySide6.QtGui import QImage, QLinearGradient, QPainter

from .catalog import COMMON, FRAMES, PALETTES, PATTERNS, Recipe
from .drawing import Inks, mix
from .imaging import HEIGHT, WIDTH, colour, ensure_gui
from .industrial import INDUSTRIAL
from .insignia import INSIGNIA
from .landscapes import scene_plate
from .materials import MATERIALS
from .natural_history import NATURAL_HISTORY
from .ornaments import draw_emblem, draw_frame
from .plates import repeat_plate
from .radial import radial_plate

_ILLUSTRATIONS = {**INSIGNIA, **NATURAL_HISTORY, **MATERIALS, **INDUSTRIAL}


def inks(palette: tuple[str, ...]) -> Inks:
    if not 2 <= len(palette) <= 5:
        raise ValueError("Choose two to five RGB colours.")
    stops = [colour(code) for code in palette]
    if len(stops) == 2:
        return stops[0], mix(*stops, 0.28), mix(*stops, 0.78), stops[-1]
    if len(stops) == 3:
        return stops[0], mix(stops[0], stops[1], 0.42), stops[1], stops[-1]
    if len(stops) == 5:
        return stops[0], stops[1], mix(stops[2], stops[3], 0.45), stops[4]
    return tuple(stops)


def render(recipe: Recipe, *, thumbnail: bool = False) -> QImage:
    """Render directly to an opaque 708 × 1016 print or a 177 × 254 gallery preview."""
    ensure_gui()
    if recipe.version != 1 or recipe.pattern not in PATTERNS or recipe.frame not in FRAMES:
        raise ValueError("Choose a supported pattern, frame and recipe version.")
    spec = PATTERNS[recipe.pattern]
    params = {s.key: s.default for s in spec.parameters}
    if recipe.params.keys() - params.keys():
        raise ValueError("Unknown control for this pattern.")
    params.update(recipe.params)
    for s in spec.parameters:
        if (
            not isinstance(params[s.key], (int, float))
            or not s.minimum <= params[s.key] <= s.maximum
        ):
            raise ValueError(f"{s.label} must be between {s.minimum} and {s.maximum}.")
    if recipe.seed is not None and not isinstance(recipe.seed, int):
        raise ValueError("Use a whole number as the design seed.")
    c = inks(recipe.palette or PALETTES[spec.palette])
    c = tuple(mix(c[0], ink, params["contrast"]) for ink in c)
    divisor = 4 if thumbnail else 1
    # Qt's native four-byte raster path is faster for antialiased line work.
    image = QImage(WIDTH // divisor, HEIGHT // divisor, QImage.Format.Format_RGB32)
    image.fill(c[0])
    with QPainter(image) as p:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(2 / divisor, 2 / divisor)
        wash = QLinearGradient(0, 0, 354, 508)
        for stop, amount in ((0, 0.53), (0.43, 0.04), (1, 0.22)):
            wash.setColorAt(stop, mix(c[0], c[1], amount))
        p.fillRect(0, 0, 354, 508, wash)
        p.save()
        p.translate(177, 254)
        p.rotate(params["angle"])
        p.scale(params["scale"], params["scale"])
        detail = params[spec.parameters[len(COMMON)].key]
        extent = math.hypot(177, 254) / params["scale"] + 10
        args = (p, recipe.pattern, c, params["density"], detail, extent)
        if illustration := _ILLUSTRATIONS.get(recipe.pattern):
            illustration(p, c, params["density"], detail, extent)
        elif recipe.pattern in (
            "fans",
            "weave",
            "stars",
            "scales",
            "tiles",
            "quilt",
            "sprigs",
            "arches",
        ):
            repeat_plate(*args)
        elif recipe.pattern in (
            "sun",
            "orbit",
            "nautilus",
            "rose",
            "eclipse",
            "guilloche",
            "compass",
            "ripple",
        ):
            radial_plate(*args)
        else:
            scene_plate(*args, random.Random(recipe.seed or 0))
        p.restore()
        draw_frame(p, recipe.frame, c)
        if recipe.emblem:
            draw_emblem(p, recipe.emblem, c)
    return image.convertToFormat(QImage.Format.Format_RGB888)

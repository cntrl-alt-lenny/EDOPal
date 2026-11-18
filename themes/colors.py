"""sRGB mixing and WCAG contrast for the surfaces the client actually draws."""

import colorsys
from dataclasses import dataclass
from functools import lru_cache

from .model import Theme

WHITE = "#ffffff"
BLACK = "#000000"


@lru_cache(maxsize=4096)
def rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix(a: str, b: str, weight: float) -> str:
    return "#" + "".join(
        f"{round(x + (y - x) * weight):02x}" for x, y in zip(rgb(a), rgb(b), strict=True)
    )


_LINEAR = tuple(
    (x / 255 / 12.92 if x / 255 <= 0.04045 else ((x / 255 + 0.055) / 1.055) ** 2.4)
    for x in range(256)
)


@lru_cache(maxsize=4096)
def luminance(color: str) -> float:
    r, g, b = rgb(color)
    return 0.2126 * _LINEAR[r] + 0.7152 * _LINEAR[g] + 0.0722 * _LINEAR[b]


def contrast(a: str, b: str) -> float:
    x, y = sorted((luminance(a), luminance(b)))
    return (y + 0.05) / (x + 0.05)


def pole(bg: str) -> str:
    return BLACK if contrast(BLACK, bg) >= contrast(WHITE, bg) else WHITE


def towards(color: str, end: str, background: str, ratio: float = 4.5) -> str:
    """Nearest mix on the requested side of the background with sufficient contrast."""
    lighter = end == WHITE

    def passes(c: str) -> bool:
        return (luminance(c) > luminance(background)) == lighter and contrast(
            c, background
        ) >= ratio

    if passes(color):
        return color
    low, high = 0.0, 1.0
    for _ in range(10):
        mid = (low + high) / 2
        if passes(mix(color, end, mid)):
            high = mid
        else:
            low = mid
    return mix(color, end, high)


def alpha(color: str, opacity: int) -> str:
    # The registry skips a packed zero even when explicitly supplied.
    return f"#{opacity:02x}" + ("000001" if opacity == 0 and color.lower() == BLACK else color[1:])


@dataclass(frozen=True)
class Bar:
    top: str
    bottom: str
    text: str


def bar(color: str, preferred_text: str) -> Bar:
    ink = pole(color)
    opposite = WHITE if ink == BLACK else BLACK
    top = towards(mix(color, WHITE, 0.12), opposite, ink)
    bottom = towards(mix(color, BLACK, 0.12), opposite, ink)
    worst = min((top, bottom), key=lambda c: contrast(c, ink))
    return Bar(top, bottom, towards(preferred_text, ink, worst))


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    raised: str
    inset: str
    selection: str
    border: str
    accent: str
    text: str
    muted: str
    highlight: str
    positive: str
    danger: str
    row: str
    row_selected: str
    bars: tuple[Bar, Bar]


@lru_cache(maxsize=48)
def derive(theme: Theme) -> Palette:
    bg, surface, accent = theme.background, theme.surface, theme.accent
    ink = pole(surface)
    paper = WHITE if ink == BLACK else BLACK

    # Reserve enough tonal range for labels on both ends of painted gradients.
    def face(color: str) -> str:
        return towards(color, paper, ink, 5.3)

    raised = face(mix(surface, ink, 0.035 + theme.depth * 0.0012))
    inset = face(mix(surface, paper, 0.07 + theme.depth * 0.0017))
    selected = face(mix(surface, accent, 0.24))
    surface = face(surface)
    worst = min((raised, inset, selected, surface), key=lambda c: contrast(c, ink))

    def label(value: str) -> str:
        return towards(value, ink, worst)

    text = label(theme.text or ("#f5f7fb" if ink == WHITE else "#162029"))
    muted = label(theme.muted or mix(surface, text, 0.65))
    hue, lightness, saturation = colorsys.rgb_to_hls(*(c / 255 for c in rgb(accent)))
    secondary_rgb = colorsys.hls_to_rgb(
        (hue + 0.40) % 1, min(0.70, max(0.48, lightness)), max(0.46, saturation)
    )
    secondary = "#" + "".join(f"{round(c * 255):02x}" for c in secondary_rgb)
    highlight = theme.highlight or secondary
    # Alpha-232 result panels may sit over a white photo. Leave headroom for
    # that compositing before checking the client's hard-coded white row text.
    row = towards(mix(surface, accent, 0.34 if ink == BLACK else 0.03), BLACK, WHITE, 6.2)
    row_selected = towards(mix(row, accent, 0.36), BLACK, WHITE, 6.2)
    return Palette(
        bg,
        surface,
        raised,
        inset,
        selected,
        mix(surface, text, 0.20 + theme.depth * 0.002),
        label(accent),
        text,
        muted,
        label(highlight),
        label(theme.positive or "#a9dc76"),
        label(theme.danger or "#ff6188"),
        row,
        row_selected,
        (bar(theme.your_lp or accent, text), bar(theme.opponent_lp or highlight, text)),
    )

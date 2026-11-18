"""Display controls for desktop EDOPro 41.0.2. Limits here are conservative editor limits."""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Option:
    title: str
    hint: str
    default: str
    choices: tuple[tuple[str, str], ...] = ()


SWITCH = (("0", "Off"), ("1", "On"))
OPTIONS = {
    "dpi_scale": Option("UI Scale", "Size of game text and controls.", "1.000000"),
    "textfont": Option(
        "Text font",
        "Menus and card descriptions share this font and size.",
        "fonts/NotoSansJP-Regular.otf 12",
    ),
    "fullscreen": Option(
        "Fullscreen (F11)", "Use the whole screen when the game starts.", "0", SWITCH
    ),
    "driver_type": Option(
        "Graphics renderer",
        "How the game draws on this device.",
        "default",
        (
            ("default", "Game default"),
            ("opengl", "OpenGL"),
            ("d3d9", "Direct3D 9"),
            ("d3d9on12", "Direct3D 9 on 12"),
            ("ogles1", "OpenGL ES 1"),
            ("ogles2", "OpenGL ES 2"),
        ),
    ),
    "vsync": Option(
        "VSync", "Match the display refresh to reduce tearing.", "1", (("0", "None"), ("1", "Full"))
    ),
    "maxFPS": Option("FPS limit if VSync off", "Maximum frames per second with VSync off.", "60"),
    "antialias": Option(
        "MSAA",
        "Smooth jagged edges. Higher levels can blur small text.",
        "0",
        (("0", "Off"), ("2", "2×"), ("4", "4×"), ("8", "8×")),
    ),
    "showFPS": Option("Show FPS", "Show the frame counter in game.", "1", SWITCH),
    "draw_field_spell": Option(
        "Draw Field Spell arts", "Show Field Spell artwork behind the cards.", "1", SWITCH
    ),
}


@dataclass(frozen=True)
class Profile:
    name: str
    hint: str
    values: dict[str, str]


PROFILES = (
    Profile(
        "Steam Deck (1280×800)",
        "Use the full handheld screen with controls at 125%.",
        {"dpi_scale": "1.25", "fullscreen": "1"},
    ),
    Profile(
        "Laptop / 1080p",
        "Use a modest 125% scale and match the screen’s refresh.",
        {"dpi_scale": "1.25", "vsync": "1"},
    ),
    Profile(
        "Large desktop / 4K",
        "Double the interface size for a high-density monitor.",
        {"dpi_scale": "2"},
    ),
    Profile(
        "Low-power (smoother, plainer)",
        "Target 30 frames per second and skip edge smoothing.",
        {"maxFPS": "30", "vsync": "0", "antialias": "0"},
    ),
)


def font_parts(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\S+) +(\d+)", value)
    if not match:
        raise ValueError("Choose an installed text font and its size.")
    return match[1], match[2]


def normalize(key: str, value: str) -> str:
    if key not in OPTIONS or not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("Unrecognised display setting.")
    if key == "dpi_scale":
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Enter a finite interface scale.")
        return f"{max(1.0, min(2.0, number)):.6f}"
    if key == "maxFPS":
        return str(max(30, min(240, int(value))))
    if key == "textfont":
        path, size = font_parts(value)
        return f"{path} {max(9, min(24, int(size)))}"
    if value not in dict(OPTIONS[key].choices):
        raise ValueError(f"Choose a listed value for {OPTIONS[key].title}.")
    return value


def equivalent(key: str, a: str | None, b: str | None) -> bool:
    if a == b:
        return True
    if a is None or b is None:
        return False
    try:
        if key == "dpi_scale":
            return float(a) == float(b)
        if key == "driver_type":
            return a.lower() == b.lower()
        if key == "textfont":
            return font_parts(a) == font_parts(b)
        return int(a) == int(b)
    except ValueError:
        return False


def describe(key: str, value: str | None) -> str:
    if value is None:
        return f"Game default ({describe(key, OPTIONS[key].default)})"
    try:
        if key == "dpi_scale":
            return f"{float(value) * 100:g}%"
        if key == "maxFPS":
            if value in ("-1", "0"):
                return "Uncapped"
            return f"{value} fps"
        if key == "textfont":
            path, size = font_parts(value)
            return f"{PurePosixPath(path).stem} · {size} px"
        if key == "vsync" and value in ("2", "3", "4"):
            return {"2": "Half", "3": "Third", "4": "Fourth"}[value]
    except ValueError:
        pass
    return dict(OPTIONS[key].choices).get(value.lower(), value)


def renderer_warning(driver: str) -> str:
    if driver == "default":
        return ""
    if (driver.startswith("d3d") and sys.platform != "win32") or (
        sys.platform == "darwin" and driver.startswith("ogles")
    ):
        return "This renderer is unavailable on this platform. Choose Game default for automatic selection."
    return "This renderer needs support from your graphics driver and may prevent EDOPro from starting."

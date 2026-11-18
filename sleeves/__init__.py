"""Window-free sleeve engine. Initialize Qt on the main thread with ensure_gui().
Run synchronous disk/render operations on a worker; CardIndex.search is memory-only
and CardArt.fetch handles its own worker. Installation functions take the active
config file path so undo journals follow the application's existing location.
"""

from .cards import ArtMissing, ArtUnavailable, Card, CardArt, CardIndex
from .catalog import EMBLEMS, FRAMES, PALETTES, PATTERNS, Emblem, Parameter, Pattern, Recipe
from .designer import render
from .imaging import Layout, compose, decode, ensure_gui, png_bytes, read_source
from .install import SlotStatus, apply, reapply, status, undo
from .library import Library
from .storage import Entry

__all__ = [
    "EMBLEMS",
    "FRAMES",
    "PALETTES",
    "PATTERNS",
    "ArtMissing",
    "ArtUnavailable",
    "Card",
    "CardArt",
    "CardIndex",
    "Emblem",
    "Entry",
    "Layout",
    "Library",
    "Parameter",
    "Pattern",
    "Recipe",
    "SlotStatus",
    "apply",
    "compose",
    "decode",
    "ensure_gui",
    "png_bytes",
    "read_source",
    "reapply",
    "render",
    "status",
    "undo",
]

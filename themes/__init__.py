"""EDOPro Studio theme engine. Disk and full-resolution export APIs belong on a worker."""

from .colors import Palette, derive
from .imaging import ensure_gui
from .install import (
    InstallState,
    install_state,
    install_theme,
    recover,
    remove_theme,
    scale_background,
    switch_back,
    undo_theme,
)
from .library import ThemeLibrary
from .model import STYLES, Theme
from .presets import PRESETS
from .preview import SCENES, render_preview
from .skin import render_skin, skin_xml
from .wallpaper import wallpaper
from .widgets import WIDGETS, widget_image

__all__ = [
    "PRESETS",
    "SCENES",
    "STYLES",
    "WIDGETS",
    "Palette",
    "Theme",
    "ThemeLibrary",
    "derive",
    "InstallState",
    "ensure_gui",
    "install_state",
    "install_theme",
    "recover",
    "remove_theme",
    "render_preview",
    "render_skin",
    "scale_background",
    "skin_xml",
    "switch_back",
    "undo_theme",
    "wallpaper",
    "widget_image",
]

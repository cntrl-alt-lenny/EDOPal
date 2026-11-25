"""EDOPal entry point and its ordered feature registry."""

from __future__ import annotations

import importlib
import os
import sys

from edopal import APP_NAME, __version__
from edopal.core.feature import FeatureSpec

FEATURES = ["hdcards", "themes", "music", "formats"]

# Keep development builds navigable while the feature packages land independently.
_PREVIEWS = {
    "hdcards": ("HD Cards", "Sync high-resolution card faces and field artwork."),
    "themes": ("Themes", "Preview game themes and choose your card backs."),
    "music": ("Music", "Preview soundtracks and assign music to each game scene."),
    "formats": ("Formats", "Browse historical events, download decks and install era banlists."),
}


def load_features(core):
    from edopal.ui.sounds_page import sounds_spec

    specs = []
    for order, name in enumerate(FEATURES):
        package = f"edopal.features.{name}"
        try:
            module = importlib.import_module(package)
        except ModuleNotFoundError as exc:
            if exc.name != package or name not in _PREVIEWS:
                raise
            module = None
        if module is not None and (hasattr(module, "register") or name not in _PREVIEWS):
            specs.append(module.register(core))
            continue
        from edopal.ui.shell import unavailable_page

        label, summary = _PREVIEWS[name]
        specs.append(FeatureSpec(name, label, name, (order + 1) * 10, unavailable_page(label, summary), summary))
    return sorted([*specs, sounds_spec()], key=lambda s: s.order)


def build_window(core, start=None):
    """Build a window around the supplied services. Pages are created on first visit."""
    from edopal.ui.shell import ShellWindow

    return ShellWindow(core, load_features(core), start)


def main() -> int:
    if "--version" in sys.argv[1:]:
        if sys.stdout is not None:  # Windows GUI launchers may have no console.
            print(__version__)
        return 0

    from PySide6.QtWidgets import QApplication

    from edopal.core.wiring import make_core
    from edopal.ui import theme

    args = sys.argv[:]
    gamescope = os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")
    if (
        sys.platform.startswith("linux")
        and gamescope
        and os.environ.get("WAYLAND_DISPLAY") == gamescope
        and os.environ.get("DISPLAY")
        and not os.environ.get("QT_QPA_PLATFORM")
        and "-platform" not in args
    ):
        # Gamescope can retain the first native Wayland frame. Prefer its XWayland display.
        args.extend(["-platform", "xcb;wayland"])
    app = QApplication.instance() or QApplication(args)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(True)
    theme.apply(app)
    core = make_core()
    window = build_window(core)
    app.setWindowIcon(window.windowIcon())
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    if "--smoke" in args:
        try:
            app.processEvents()
            return int(window.grab().isNull())
        finally:
            window.close()
    window.check_updates()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

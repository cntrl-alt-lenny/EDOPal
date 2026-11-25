"""Open the bundled owner build directly while the gallery package awaits publication."""

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from edopal.core.wiring import make_core
from edopal.ui import theme

from .page import TrackerPage


def main():
    parser = argparse.ArgumentParser(description="Open EDOPal’s local Deck Tracker.")
    parser.add_argument("--game", type=Path, help="EDOPro installation (defaults to the one selected in EDOPal)")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("EDOPal")
    theme.apply(app)
    core = make_core()
    if args.game:
        core.installs.select(args.game)
    page = TrackerPage(core)
    page.setWindowTitle("EDOPal · Deck Tracker")
    page.resize(1150, 760)
    page.show()
    app.aboutToQuit.connect(core.jobs.shutdown)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

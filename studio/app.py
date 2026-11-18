import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from config import Config
from studio import theme
from studio.context import AppContext
from studio.shell import StudioWindow
from studio.tasks import RunCoro


def run_app(cfg: Config, version: str, run_coro: RunCoro, apply_choices: Callable) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("EDOPro Studio")
    theme.apply(app)
    image = theme.ASSETS / "app-icon.png"
    if not image.is_file():
        image = Path(getattr(sys, "_MEIPASS", theme.ASSETS)) / "app-icon.png"
    app.setWindowIcon(QIcon(str(image)))
    ctx = AppContext(cfg, version, run_coro, apply_choices)
    window = StudioWindow(ctx)
    window.show()
    app.exec()
    return ctx.exit_code

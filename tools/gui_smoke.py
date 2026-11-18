"""Offline startup probe for the installed Qt payload."""

import os
import sys
import threading
from collections.abc import Callable

from config import APP_NAME, Config
from studio.tasks import RunCoro


def smoke_test(cfg: Config, version: str, run: RunCoro, apply: Callable) -> int:
    def timeout() -> None:
        print("GUI smoke test timed out after 30 seconds", file=sys.stderr, flush=True)
        os._exit(1)

    # A timer on Qt's thread could never fire if window construction stalled.
    watchdog = threading.Timer(30, timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        return _show_and_check(cfg, version, run, apply)
    finally:
        watchdog.cancel()


def _show_and_check(cfg: Config, version: str, run: RunCoro, apply: Callable) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFontDatabase, QIcon, QImageReader
    from PySide6.QtWidgets import QApplication

    from studio import theme
    from studio.context import AppContext
    from studio.shell import StudioWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    app.setWindowIcon(QIcon(str(theme.ASSETS / "app-icon.png")))
    window = StudioWindow(AppContext(cfg, version, run, apply), startup=False)
    window.show()
    platform = app.platformName()
    print(f"Qt platform: {platform}", flush=True)
    if sys.platform == "darwin":
        print(f"Process architecture: {os.uname().machine}", flush=True)
    passed = False

    def inspect() -> None:
        nonlocal passed
        try:
            expected = os.environ.get("QT_QPA_PLATFORM")
            native = {"win32": {"windows"}, "darwin": {"cocoa"}, "linux": {"wayland", "xcb"}}
            if platform not in ({expected} if expected else native[sys.platform]):
                raise RuntimeError(f"Requested Qt platform {expected}, loaded {platform}")
            if not window.isVisible() or window.windowHandle() is None:
                raise RuntimeError("Studio window did not open")
            for filename, codec in (
                ("app-icon.png", b"png"),
                ("window/studio-vortex.jpg", b"jpeg"),
            ):
                reader = QImageReader(str(theme.ASSETS / filename))
                if bytes(reader.format()) != codec or reader.read().isNull():
                    raise RuntimeError(f"Could not decode {filename}: {reader.errorString()}")
            font = QFontDatabase.addApplicationFont(
                str(theme.ASSETS / "window/fonts/Inter-Medium.ttf")
            )
            if not QFontDatabase.applicationFontFamilies(font):
                raise RuntimeError("Could not load bundled Inter font")
        except Exception as exc:
            print(f"GUI smoke test failed: {exc}", file=sys.stderr, flush=True)
            app.exit(1)
        else:
            passed = True
            print("GUI smoke test passed.", flush=True)
            app.exit(0)

    QTimer.singleShot(0, inspect)
    code = app.exec()
    if not code and not passed:
        print("Window closed before the smoke test finished", file=sys.stderr, flush=True)
        return 1
    return code

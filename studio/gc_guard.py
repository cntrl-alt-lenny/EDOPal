"""Keep Python's cyclic garbage collector on the GUI thread.

A collection can start on whichever thread happens to allocate next. When that is
a worker thread and the garbage includes Qt objects, they are destroyed off the
GUI thread, which crashes natively (seen as a segmentation fault on Linux and an
access violation on Windows, always "Garbage-collecting" inside a worker).
Automatic collection is therefore switched off while the Qt app runs, and a GUI
timer performs the same generational collections instead.
"""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer
from shiboken6 import isValid

INTERVAL_MS = 1000


class Collector(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.thresholds = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.setInterval(INTERVAL_MS)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def check(self) -> None:
        """Collect what automatic collection would have, on the calling (GUI) thread."""
        young, middle, old = gc.get_count()
        if young <= self.thresholds[0]:
            return
        gc.collect(0)
        if middle > self.thresholds[1]:
            gc.collect(1)
            if old > self.thresholds[2]:
                gc.collect(2)


_collector: Collector | None = None


def install() -> Collector | None:
    """Idempotent. Does nothing without a Qt application or off the GUI thread."""
    global _collector
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        return None
    if _collector is None or not isValid(_collector):
        _collector = Collector(app)
    return _collector

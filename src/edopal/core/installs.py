"""Selected EDOPro installation and the Qt-free implementation of InstallService."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from edopal.core import discovery, process
from edopal.core.install import Install, RunState
from edopal.core.launch import launch
from edopal.core.settings import Settings

_log = logging.getLogger(__name__)


class RealInstalls:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[Install | None], None]] = []
        self._current: Install | None = None
        saved = settings.get("install", "root")
        if isinstance(saved, str) and Path(saved).is_absolute():
            try:
                self._current = discovery.inspect_selected(Path(saved))
            except ValueError:
                # Removable media may be absent. Keep the saved preference for its return.
                pass

    def current(self) -> Install | None:
        with self._lock:
            return self._current

    def discover(self) -> list[Install]:
        """Collect picker options in a worker. Selecting a row is a separate GUI action."""
        saved = self._settings.get("install", "root")
        hints = [Path(saved)] if isinstance(saved, str) and Path(saved).is_absolute() else []
        return discovery.discover(hints)

    def select(self, root: Path) -> Install:
        selected = discovery.inspect_selected(root)
        with self._lock:
            previous = self._current
            if previous is None or previous.root != selected.root:
                self._settings.set("install", "root", str(selected.root))
            self._current = selected
            changed = previous is None or previous != selected or previous.extra != selected.extra
            subscribers = tuple(self._subscribers) if changed else ()
        for callback in subscribers:
            try:
                callback(selected)
            except Exception:
                _log.exception("EDOPro selection callback failed")
        return selected

    def subscribe(self, callback: Callable[[Install | None], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def run_state(self, install: Install | None = None) -> RunState:
        selected = install or self.current()
        return process.run_state(selected) if selected else RunState.UNKNOWN

    def launch(self) -> str:
        selected = self.current()
        if selected is None:
            raise ValueError("Choose an EDOPro folder first.")
        return launch(selected)

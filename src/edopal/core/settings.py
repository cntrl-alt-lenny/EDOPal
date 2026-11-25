"""Small JSON settings store, namespaced per feature (Qt-free)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

SCHEMA = 1


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"schema": SCHEMA}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except (OSError, ValueError):
            pass

    def get(self, ns: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(ns, {}).get(key, default)

    def set(self, ns: str, key: str, value: Any) -> None:
        with self._lock:
            self._data.setdefault(ns, {})[key] = value
            self._save()

    def section(self, ns: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data.get(ns, {}))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

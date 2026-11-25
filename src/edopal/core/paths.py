"""Per-user locations (Qt-free). Override the root with EDOPAL_HOME for tests."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _base(kind: str) -> Path:
    if override := os.environ.get("EDOPAL_HOME"):
        return Path(override) / kind
    home = Path.home()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA" if kind == "cache" else "APPDATA", home))
        return root / "EDOPal" / ("Cache" if kind == "cache" else "")
    if sys.platform == "darwin":
        lib = home / "Library"
        return (lib / "Caches" / "EDOPal") if kind == "cache" else (lib / "Application Support" / "EDOPal")
    env = {"config": "XDG_CONFIG_HOME", "data": "XDG_DATA_HOME", "cache": "XDG_CACHE_HOME"}[kind]
    default = {"config": ".config", "data": ".local/share", "cache": ".cache"}[kind]
    return Path(os.environ.get(env) or home / default) / "edopal"


@dataclass(frozen=True)
class Paths:
    config: Path  # settings.json
    data: Path  # undo journals, receipts (durable)
    cache: Path  # downloaded packs, thumbnails, HTTP cache (disposable)

    @classmethod
    def default(cls) -> Paths:
        return cls(_base("config"), _base("data"), _base("cache"))

    def ensure(self) -> Paths:
        for p in (self.config, self.data, self.cache):
            p.mkdir(parents=True, exist_ok=True)
        return self

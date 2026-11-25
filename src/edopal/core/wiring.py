"""Compose application services at startup. Features depend only on the core contracts."""

from __future__ import annotations

import os
from pathlib import Path

from edopal.core import Core
from edopal.core.fakes import DirectStorage, LocalPacks, StaticInstalls
from edopal.core.paths import Paths
from edopal.core.settings import Settings


def make_core() -> Core:
    from edopal.ui.jobs import QtJobRunner

    paths = Paths.default().ensure()
    settings = Settings(paths.config / "settings.json")
    try:
        from edopal.core.installs import RealInstalls
    except ImportError as exc:
        if exc.name != "edopal.core.installs":
            raise
        installs = StaticInstalls(None)
    else:
        installs = RealInstalls(settings)

    try:
        from edopal.core.safe_storage import SafeStorage
    except ImportError as exc:
        if exc.name != "edopal.core.safe_storage":
            raise
        storage = DirectStorage(paths.data)
    else:
        storage = SafeStorage(paths, installs.run_state)

    override = os.environ.get("EDOPAL_PACKS_DIR")
    local = Path(override).expanduser() if override else None
    try:
        from edopal.core.pack_client import GitHubPacks
    except ImportError as exc:
        if exc.name != "edopal.core.pack_client":
            raise
        packs = LocalPacks(local or Path(__file__).resolve().parents[3] / "packs", paths.cache)
    else:
        packs = GitHubPacks(paths, local_dir=local)

    return Core(paths, settings, installs, storage, packs, QtJobRunner())

"""Shared services handed to every feature. Qt-free except ``jobs`` (runner implementation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edopal.core.install import InstallService
from edopal.core.jobs import JobRunner
from edopal.core.packs import PackService
from edopal.core.paths import Paths
from edopal.core.settings import Settings
from edopal.core.storage import Storage


@dataclass
class Core:
    paths: Paths
    settings: Settings
    installs: InstallService
    storage: Storage
    packs: PackService
    jobs: JobRunner
    # Shell services for features (set by the shell; no-ops in tests):
    notify: Any = field(default=lambda text, kind="info": None)  # toast: kind in info|success|warning|error
    navigate: Any = field(default=lambda feature_id: None)

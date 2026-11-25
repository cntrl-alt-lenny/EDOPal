"""Game installation contract: discovery, selection, launch and running state (Qt-free).

Implementation lives in this package (``discovery.py``, ``launch.py``, ``process.py``);
features only use :class:`InstallService`.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Kind = Literal["native", "steam-shortcut", "proton", "flatpak", "macos-app", "windows", "unknown"]


class RunState(enum.Enum):
    CLOSED = "closed"
    RUNNING = "running"
    UNKNOWN = "unknown"  # could not tell; writes must ask the user to confirm the game is closed


@dataclass(frozen=True)
class LaunchTarget:
    """How to start this copy of EDOPro. ``argv`` is run with ``cwd`` unless ``uri`` is set,
    in which case the URI (e.g. steam://rungameid/<id>) is opened through the OS handler."""

    argv: tuple[str, ...] = ()
    cwd: Path | None = None
    uri: str | None = None
    description: str = ""  # e.g. "Steam (Proton)"


@dataclass(frozen=True)
class Install:
    root: Path  # canonical data root: the folder holding cards.cdb, pics/, skin/, sound/, config/
    kind: Kind = "unknown"
    version: str | None = None  # e.g. "41.0.2"
    launch: LaunchTarget | None = None
    label: str = ""  # human name, e.g. "ProjectIgnis (Steam)"
    ready: bool = True  # False = folder found but card databases not downloaded yet
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)


class InstallService(Protocol):
    def current(self) -> Install | None: ...
    def discover(self) -> list[Install]: ...  # may be slow; call from a job
    def select(self, root: Path) -> Install: ...  # validates + persists the choice; raises ValueError
    def launch(self) -> str: ...  # starts the current install; returns a status line; raises on failure
    def run_state(self) -> RunState: ...
    def subscribe(self, callback: Callable[[Install | None], None]) -> None: ...  # selection changed

"""Feature registration contract.

A feature is one folder under ``edopal/features/<id>/`` whose ``__init__.py`` exposes
``register(core) -> FeatureSpec``. ``edopal/app.py`` lists features explicitly (frozen builds
need the import). Adding a feature = one folder + one line in ``app.FEATURES``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from edopal.core import Core


@dataclass(frozen=True)
class FeatureSpec:
    id: str  # "hdcards", "themes", "music", "formats"
    label: str  # sidebar text, e.g. "HD Cards"
    icon: str  # icon name in edopal/assets/icons/<icon>.svg (24x24, stroke = currentColor)
    order: int  # sidebar position (lower first)
    page: Callable[[Core], Any]  # lazy factory returning a QWidget; called once on first visit
    summary: str = ""  # one line, used in tooltips / about
    # Optional hooks the shell calls; all run on the GUI thread.
    on_install_changed: Callable[[Core], None] | None = None
    on_game_exited: Callable[[Core], None] | None = None  # e.g. HD Cards re-checks for new cards
    badge: Callable[[Core], str | None] | None = field(default=None)  # sidebar badge text, e.g. "12"
    kind: Literal["feature", "plugin"] = "feature"
    section: str | None = None  # custom library heading for features; plugins use the Plugins section

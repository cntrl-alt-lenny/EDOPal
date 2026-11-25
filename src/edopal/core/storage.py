"""Safe writes into the game folder (Qt-free).

Every feature that modifies the game goes through a :class:`ChangeSet`:

    with core.storage.changeset(install, feature="themes", title="Apply Abyss") as cs:
        cs.write("skin/EDOPal-abyss/skin.xml", data)
        cs.copy_in(src_path, "textures/cover.png")
        cs.set_conf("config/system.conf", "skin", "EDOPal-abyss")
        cs.remove("sound/BGM/deck/old.mp3")
    # commit happens on clean exit: lock -> game-closed check -> stage -> journal -> atomic publish

Undo restores the last committed changeset of a feature, skipping files changed since
(reported as conflicts). Paths are always relative to ``install.root``; escaping it raises.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from edopal.core.install import Install


class GameRunningError(RuntimeError):
    """Raised on commit when EDOPro is running (or UNKNOWN and not confirmed)."""


@dataclass(frozen=True)
class Receipt:
    feature: str
    title: str
    changed: tuple[str, ...]
    created_at: float
    id: str


@dataclass(frozen=True)
class UndoResult:
    restored: tuple[str, ...]
    conflicts: tuple[str, ...] = field(default=())  # files edited by someone else since; left alone


class ChangeSet(Protocol):
    def write(self, rel: str, data: bytes) -> None: ...
    def copy_in(self, src: Path, rel: str) -> None: ...
    def remove(self, rel: str) -> None: ...
    def set_conf(self, rel: str, key: str, value: str) -> None: ...  # "key = value" line files
    def __len__(self) -> int: ...


class Storage(Protocol):
    def changeset(
        self, install: Install, feature: str, title: str, *, confirmed_closed: bool = False
    ) -> AbstractContextManager[ChangeSet]: ...
    def last_receipt(self, install: Install, feature: str) -> Receipt | None: ...
    def undo_last(self, install: Install, feature: str, *, confirmed_closed: bool = False) -> UndoResult: ...
    def history(self, install: Install) -> Iterator[Receipt]: ...

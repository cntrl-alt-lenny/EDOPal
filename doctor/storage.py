"""Cancellable read-only inventory of file sizes on disk."""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config import APP_NAME


class ScanCancelled(Exception):
    """The caller cancelled the storage scan."""


@dataclass(frozen=True)
class StorageCategory:
    id: str
    title: str
    files: int
    bytes: int


@dataclass(frozen=True)
class StorageBreakdown:
    categories: tuple[StorageCategory, ...]
    unreadable: tuple[Path, ...]

    @property
    def total_bytes(self) -> int:
        return sum(category.bytes for category in self.categories)


_GAME = {
    "artwork": ("Artwork", ("pics",)),
    "decks": ("Decks", ("deck",)),
    "replays": ("Replays", ("replay",)),
    "appearance": ("Skins, textures and fonts", ("skin", "textures", "fonts")),
    "audio": ("Game audio", ("sound",)),
    "updates": ("Update repositories and puzzles", ("repositories", "puzzles")),
    "game_data": (
        "Card data, scripts and lists",
        ("expansions", "script", "lflists", "cards.cdb", "lflist.conf"),
    ),
    "settings": ("Game settings", ("config",)),
    "windbot": ("WindBot", ("WindBot",)),
    "screenshots": ("Screenshots", ("screenshots",)),
    "other": ("Other game files", ()),
}
_APP = {
    "soundtracks": (f"{APP_NAME} soundtracks", ("soundtracks",)),
    "backups": (f"{APP_NAME} backups", ("extras-backups",)),
    "studio_state": (f"Other {APP_NAME} data", ()),
}


def _redirected(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def storage_breakdown(
    installation: str | Path,
    *,
    data_dir: str | Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> StorageBreakdown:
    """Count logical bytes per category. Call on a worker and pass Event.is_set to cancel."""
    game = Path(installation).resolve()
    app = Path(data_dir).resolve() if data_dir is not None else None
    if app == game:
        app = None
    categories = _GAME if app is None else _GAME | _APP
    totals = {id: [0, 0] for id in categories}
    unreadable: list[Path] = []

    def check_cancel() -> None:
        if cancelled is not None and cancelled():
            raise ScanCancelled

    def scan(
        root: Path, rules: dict[str, tuple[str, tuple[str, ...]]], other: str, exclude: Path | None
    ) -> None:
        category_by_name = {
            name.casefold(): id for id, (_, names) in rules.items() for name in names
        }

        def walk(folder: Path, category: str | None) -> None:
            check_cancel()
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        check_cancel()
                        path = Path(entry.path)
                        if path == exclude:
                            continue
                        id = category or category_by_name.get(entry.name.casefold(), other)
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            unreadable.append(path)
                            continue
                        if _redirected(info):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            walk(path, id)
                        elif stat.S_ISREG(info.st_mode):
                            totals[id][0] += 1
                            totals[id][1] += info.st_size
            except OSError:
                unreadable.append(folder)

        walk(root, None)

    scan(game, _GAME, "other", app)
    if app is not None:
        scan(app, _APP, "studio_state", game)
    return StorageBreakdown(
        tuple(StorageCategory(id, title, *totals[id]) for id, (title, _) in categories.items()),
        tuple(sorted(unreadable)),
    )

"""Blocking sleeve services. All callers dispatch these operations to the Studio pool."""

import colorsys
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

import extras
import sleeves
import themes
from config import folder_has_card_databases
from sleeves import Library, SlotStatus
from studio.context import AppContext
from studio.tasks import WorkerContext

from .skins_model import preset_id
from .sleeves_collection import Choice, collection, saved


@cache
def prints() -> dict[Path, Choice]:
    return {item.key: item for item in collection()}


def small(image: QImage) -> QImage:
    return image.scaled(
        177, 254, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


class Thumbnails:
    def __init__(self, library: Library) -> None:
        self.library = library

    def __call__(self, key: Path, ctx: WorkerContext) -> QImage:
        if ctx.cancel.is_set():
            return QImage()
        if item := prints().get(key):
            return sleeves.render(item.recipe, thumbnail=True)
        return QImage(str(self.library.thumbnail(key.name)))

    def colours(self, items: list[Choice], ctx: WorkerContext) -> dict[Path, str]:
        families = {}
        for item in items:
            if ctx.cancel.is_set():
                break
            try:
                image = self(item.key, ctx).scaled(
                    1,
                    1,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            except (OSError, ValueError, KeyError):
                families[item.key] = ""
                continue
            if not image.isNull():
                r, g, b, _ = image.pixelColor(0, 0).getRgbF()
                hue, saturation, _ = colorsys.rgb_to_hsv(r, g, b)
                if saturation < 0.14:
                    name = "Neutral"
                else:
                    name = next(
                        name
                        for limit, name in (
                            (18, "Red"),
                            (65, "Gold"),
                            (177, "Green"),
                            (255, "Blue"),
                            (325, "Purple"),
                            (361, "Red"),
                        )
                        if hue * 360 < limit
                    )
                families[item.key] = name
        return families


@dataclass
class Slot:
    status: SlotStatus
    choice: Choice | None
    image: QImage


@dataclass
class Snapshot:
    items: list[Choice]
    removed: list[Choice]
    slots: dict[str, Slot]
    can_undo: bool
    active_name: str


def inspect(library: Library, game: Path | None, config: Path) -> Snapshot:
    prints()  # Prepare the immutable gallery on the collection worker.
    items = [saved(e) for e in library.entries()]
    removed = [saved(e) for e in library.entries(in_bin=True)]
    by_id = {item.entry.id: item for item in (*items, *removed)}
    by_hash = {item.entry.image: item for item in (*items, *removed)}
    slots = {}
    if game is not None:
        for key, status in sleeves.status(library, game).items():
            choice = by_id.get(status.entry_id)
            image = QImage()
            if status.path:
                try:
                    data = sleeves.read_source(status.path)
                    image = small(sleeves.decode(data))
                    if not choice:
                        from hashlib import sha256

                        choice = by_hash.get(sha256(data).hexdigest())
                except (OSError, ValueError):
                    pass  # An unreadable installed cover can still be replaced or restored.
            slots[key] = Slot(status, choice, image)
    active = extras.current_skin(str(game)) if game else ""
    if match := re.fullmatch(r"Studio-([a-f0-9]{32})(?:-[1-9][0-9]*)?", active):
        lib = themes.ThemeLibrary(library.root.parent / "themes")
        key = match[1]
        theme = next((p for p in themes.PRESETS if preset_id(p) == key), None)
        if not theme:
            try:
                theme = lib.load(key)
            except (ValueError, OSError):
                # A deleted library copy can still be installed and have usable sleeves.
                theme = None
        active = theme.name if theme else "Installed theme"
    return Snapshot(
        items,
        removed,
        slots,
        bool(game and extras.can_undo(str(game), str(config), "sleeves")),
        active.removeprefix("Monokai "),
    )


def check_closed(game: Path) -> None:
    if not game.is_dir() or not folder_has_card_databases(str(game)):
        raise ValueError(
            "The game folder is no longer ready. Reconnect it or choose another folder."
        )
    AppContext.require_game_closed(game)


def write(
    library: Library, game: Path, config: Path, action: str, choices: dict[str, Choice]
) -> None:
    check_closed(game)
    if action == "undo":
        sleeves.undo(library, game, config)
    elif action == "reapply":
        sleeves.reapply(library, game, config)
    else:
        known = library.entries()
        resolved = {}
        for slot, item in choices.items():
            if item.entry:
                ref = item.entry
            else:
                ref = next((e for e in known if e.recipe == item.recipe), None)
                if ref is None:
                    ref = library.add_design(item.full_name, item.recipe)
                    known.append(ref)
            resolved[slot] = ref.id
        sleeves.apply(library, game, config, **resolved)

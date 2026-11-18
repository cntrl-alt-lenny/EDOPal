"""Remember each slot independently and report the effective file in the current skin."""

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import extras

from .library import Library
from .storage import Catalog, digest

Slot = Literal["mine", "opponent"]
_BASES: dict[Slot, str] = {"mine": "cover", "opponent": "cover2"}


def install_key(installation: Path) -> str:
    return os.path.normcase(str(installation.resolve()))


@dataclass(frozen=True)
class SlotStatus:
    entry_id: str | None
    path: Path | None
    matches: bool | None  # None means the app has no recorded preference.


def status(library: Library, installation: Path) -> dict[Slot, SlotStatus]:
    """Resolve the current skin each time. A missing opponent cover follows the player."""
    with library._store.open() as state:
        slots = state.assignments.get(install_key(installation), {})
        active = extras.current_skin(str(installation))
        result = {}
        for slot, base in _BASES.items():
            path = extras.installed_sleeve(installation, base, active)
            ref = slots.get(slot)
            matches = (
                None
                if ref is None
                else bool(path and extras._signature(path) == state.entries[ref].image)
            )
            # Show EDOPro's fallback, while flagging a missing independent assignment.
            if slot == "opponent" and path is None:
                path = result["mine"].path
            result[slot] = SlotStatus(ref, path, matches)
        return result


def apply(
    library: Library,
    installation: Path,
    config_path: Path,
    *,
    mine: str | None = None,
    opponent: str | None = None,
) -> tuple[Path, ...]:
    """Apply supplied entry IDs together. Omitted slots keep their preference and files."""
    selected = {
        slot: ref for slot, ref in (("mine", mine), ("opponent", opponent)) if ref is not None
    }
    return _change(library, installation, config_path, selected)


def reapply(library: Library, installation: Path, config_path: Path) -> tuple[Path, ...]:
    """Call after an app-managed skin change to carry both saved choices forward."""
    return _change(library, installation, config_path, None)


def undo(library: Library, installation: Path, config_path: Path) -> tuple[Path, ...]:
    """Undo the last changed sleeve transaction. Externally edited files raise ValueError."""
    return _change(library, installation, config_path, {}, restoring=True)


def _change(
    library: Library,
    installation: Path,
    config_path: Path,
    selected: dict[str, str] | None,
    *,
    restoring: bool = False,
) -> tuple[Path, ...]:
    root = installation.resolve()
    store = library._store
    journal_dir = extras._group_dir(root, str(config_path), "sleeves")
    prefix = os.path.normcase(str(journal_dir)) + "|"
    with store.open(writable=True) as state:
        before = state.encode()
        slots = state.assignments.setdefault(install_key(root), {})
        choices = slots.copy() if selected is None else selected

        def prepare(changes: dict) -> None:
            # Save intent under both locks, before touching textures. A process exit
            # leaves either the chosen files or a detectable, re-applicable preference.
            # Match undo metadata to the actual journal, including interrupted installs.
            record = prefix + digest(json.dumps(changes).encode())
            if restoring:
                prior = state.undo.get(record)
                if prior is None:
                    return  # A legacy gallery operation has no Studio preference history.
                for name in changes:
                    slot = "mine" if Path(name).stem == "cover" else "opponent"
                    if slot in prior:
                        slots[slot] = prior[slot]
                    else:
                        slots.pop(slot, None)
            else:
                if changes:
                    last = extras._signature(journal_dir / "last.json")
                    for key in list(state.undo):
                        if key.startswith(prefix) and key != prefix + (last or ""):
                            del state.undo[key]
                    state.undo[record] = slots.copy()
                slots.update(choices)
                now = time.time()
                for ref in choices.values():
                    state.entries[ref] = replace(state.entries[ref], last_used=now)
            store.save(state)

        try:
            if restoring:
                changed = extras._change_group(
                    root, str(config_path), "sleeves", None, prepare=prepare
                )
            else:
                data = {slot: library._bytes(state.entries[ref]) for slot, ref in choices.items()}
                changed = extras.apply_sleeve_pngs(
                    root,
                    str(config_path),
                    mine=data.get("mine"),
                    opponent=data.get("opponent"),
                    prepare=prepare,
                )
        except Exception:
            if state.encode() != before:
                store.save(Catalog.load(json.loads(before)))
            raise
        return tuple(root / name for name in changed)

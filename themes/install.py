"""Public, synchronous game operations. Call from the page's I/O worker."""

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QImage

import extras
from state_lock import appearance_lock

from .model import Theme
from .skin import render_skin
from .transaction import MARKER, Store, recover_locked

_SCALE = re.compile(rb"(?m)^[ \t]*scale_background[ \t]*=[ \t]*([01])\b")
_FOLDER = re.compile(r"Studio-([a-f0-9]{32})(?:-[1-9][0-9]*)?")


@dataclass(frozen=True)
class InstallState:
    folder: str | None
    active: bool
    can_undo: bool
    recovery_pending: bool


def install_state(game: Path, theme_id: str) -> InstallState:
    """Read page action availability without writing or initializing the game directory."""
    store = Store(game.resolve(strict=True), theme_id)
    record = store.record()
    folder = record["current"]["folder"] if record["current"] else None
    return InstallState(
        folder,
        bool(folder and store.config()[1].get("skin") == folder),
        record["undo"] is not None,
        store.pending.exists(),
    )


def scale_background(game: Path) -> bool:
    path = game / "config/system.conf"
    content = path.read_bytes().removeprefix(b"\xef\xbb\xbf") if path.is_file() else b""
    matches = _SCALE.findall(content)
    return not matches or matches[-1] == b"1"


def _folder(store: Store) -> str:
    base = "Studio-" + store.id
    parent = store.root / "skin"
    taken = {path.name.casefold() for path in parent.iterdir()} if parent.is_dir() else set()
    folder = base
    i = 1
    while folder.casefold() in taken:
        i += 1
        folder = f"{base}-{i}"
    return folder


def _reference(key: str, value: str | None) -> tuple[str, str]:
    parts = (value or "").split("/")
    if key == "skin":
        return value or "", "skin.xml"
    if len(parts) >= 3 and parts[0] == "skin":
        return parts[1], "/".join(parts[2:]).split(" ")[0]
    return "", ""


def _history(store: Store, key: str, value: str | None, own_folder: str) -> list:
    # Capture the chain now. A later A -> B -> A switch must still lead back
    # to the original game skin if either predecessor is subsequently removed.
    folder, _ = _reference(key, value)
    match = _FOLDER.fullmatch(folder)
    chain = [value]
    if match:
        manifest = Store(store.root, match[1]).record()["current"]
        if manifest and manifest["folder"] == folder:
            chain.extend(manifest["previous"].get(key, []))
    return list(dict.fromkeys(v for v in chain if _reference(key, v)[0] != own_folder)) or [None]


def _available(store: Store, key: str, chain: list, restoring: dict | None = None) -> str | None:
    for value in chain:
        folder, filename = _reference(key, value)
        match = _FOLDER.fullmatch(folder)
        if not match:
            return value
        candidate = Store(store.root, match[1])
        if not candidate.record_path.exists():
            return value
        manifest = restoring if candidate.id == store.id else candidate.record()["current"]
        if manifest and manifest["folder"] == folder and filename in manifest["files"]:
            return value
        # A clashing third-party folder can have exactly this name.
        if (
            filename == "skin.xml"
            and (store.root / "skin" / folder / filename).is_file()
            and (not (store.root / "skin" / folder / MARKER).exists())
        ):
            return value
    return None


def _restore(store: Store, manifest: dict, values: dict) -> dict:
    return {
        key: [applied, _available(store, key, manifest["previous"][key])]
        for key, applied in manifest["applied"].items()
        if values.get(key) == applied
    }


def install_theme(game: Path, theme_id: str, theme: Theme, *, image: QImage | None = None) -> str:
    """Install or edit a theme, activate last, and return its stable game folder name."""
    root = game.resolve(strict=True)
    with appearance_lock(root):
        recover_locked(root)
        store = Store(root, theme_id)
        record = store.record()
        old = record["current"]
        if old:
            store.verify(old)
        folder = old["folder"] if old else _folder(store)
        files = render_skin(theme, image=image, scale_background=scale_background(root))
        applied = {"skin": folder}
        if theme.font == "inter":
            # Private copies keep a font edit or removal independent of legacy packs.
            applied.update({k: f"skin/{folder}/{v}" for k, v in extras.FONT_SETTINGS.items()})
        _, values = store.config()
        previous = {key: _history(store, key, values.get(key), folder) for key in applied}
        changes = {}
        if old:
            for key, value in old["applied"].items():
                if values.get(key) != value:
                    continue
                if key in applied:
                    previous[key] = old["previous"][key]
                else:
                    changes[key] = [value, _available(store, key, old["previous"][key])]
        changes.update({key: [values.get(key), value] for key, value in applied.items()})
        changes = {k: pair for k, pair in changes.items() if pair[0] != pair[1]}
        needed = {name.split("/")[0] for name in files if "/" in name}
        owned_dirs = sorted(
            d
            for d in needed
            if (old and d in old["dirs"]) or not (root / "skin" / folder / d).exists()
        )
        try:
            new = {
                "folder": folder,
                "owner": old["owner"] if old else uuid4().hex,
                "files": store.stage(files),
                "previous": previous,
                "applied": applied,
                "dirs": owned_dirs,
            }
            if old != new or changes:
                next_record = {"current": new, "undo": {"prior": old, "settings": changes}}
                store.commit("install", old, next_record, changes)
        except Exception:
            if not store.pending.exists():
                store.collect(store.record())
            raise
        return folder


def switch_back(game: Path, theme_id: str) -> None:
    """Restore the prior selection and its owned font keys, leaving all skin files in place."""
    root = game.resolve(strict=True)
    with appearance_lock(root):
        recover_locked(root)
        store = Store(root, theme_id)
        current = store.record()["current"]
        if current is None:
            raise ValueError("This theme is not installed.")
        _, values = store.config()
        if values.get("skin") == current["folder"]:
            store.patch_settings(_restore(store, current, values))


def _reverse(game: Path, theme_id: str, action: str) -> None:
    root = game.resolve(strict=True)
    with appearance_lock(root):
        recovered = recover_locked(root)
        if recovered and (recovered["id"], recovered["action"]) == (theme_id, action):
            return
        store = Store(root, theme_id)
        record = store.record()
        old = record["current"]
        if old is None:
            raise ValueError("This theme is not installed.")
        _, values = store.config()
        new = None
        if action == "remove":
            changes = _restore(store, old, values)
        else:
            undo = record["undo"]
            if undo is None:
                raise ValueError("There is no theme change to undo.")
            new = undo["prior"]
            active = values.get("skin") == old["folder"]
            changes = {
                k: [after, _available(store, k, [before, *old["previous"].get(k, [])], new)]
                for k, (before, after) in undo["settings"].items()
                if values.get(k) == after and (active or _reference(k, after)[0] == old["folder"])
            }
            if new is None:
                changes.update(_restore(store, old, values))
        next_record = {"current": new, "undo": None}
        store.commit(action, old, next_record, changes)


def undo_theme(game: Path, theme_id: str) -> None:
    """Undo one installation or edit, preserving subsequent independent settings."""
    _reverse(game, theme_id, "undo")


def remove_theme(game: Path, theme_id: str) -> None:
    """Verify the complete manifest before removing any owned file."""
    _reverse(game, theme_id, "remove")


def recover(game: Path) -> None:
    """Finish any interrupted game operation. Safe to repeat."""
    root = game.resolve(strict=True)
    with appearance_lock(root):
        recover_locked(root)

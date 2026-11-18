"""File dependencies of the editable Studio library formats."""

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import PurePosixPath

from sleeves.imaging import MAX_BYTES
from sleeves.storage import Catalog
from themes.imaging import MAX_IMAGE_BYTES
from themes.model import MAX_DEFINITION, Theme

from .core import MAX_FILE, MAX_FILES, MAX_MANIFEST, Cancel, Digest, Installation, check, read_small

THEME_ID = re.compile(r"[0-9a-f]{32}\.json")
LEGACY_THEME = re.compile(r"[0-9a-f]{32} \(incoming(?: [0-9]+)?\)\.json")
THEME_FOLDERS = {"studio/themes", "studio/themes/removed"}
CATALOGUES = {"studio/sleeves/manifest.json", "studio/sleeves/manifest.backup.json"}


def recipe(name: str) -> bool:
    path = PurePosixPath(name)
    return str(path.parent) in THEME_FOLDERS and bool(
        THEME_ID.fullmatch(path.name) or LEGACY_THEME.fullmatch(path.name)
    )


def restored_name(name: str) -> str:
    parent, _, base = name.rpartition("/")
    if parent in THEME_FOLDERS and LEGACY_THEME.fullmatch(base):
        # An earlier backup could itself contain a retained theme with the old suffix.
        stem = hashlib.sha256(base.encode("ascii")).hexdigest()[:32]
        return f"{parent}/{stem}.json"
    return name


def asset_digest(name: str) -> str | None:
    parent, _, base = name.rpartition("/")
    if parent == "studio/sleeves/images" and base.endswith(".png"):
        base = base[:-4]
    elif parent not in {"studio/sleeves/originals", "studio/themes/images"}:
        return None
    return base if Digest.fullmatch(base) else None


def size_limit(name: str) -> int:
    if recipe(name):
        return MAX_DEFINITION
    if name in CATALOGUES:
        return MAX_MANIFEST
    if asset_digest(name):
        return MAX_IMAGE_BYTES if name.startswith("studio/themes/") else MAX_BYTES
    return MAX_FILE


def references(name: str, content: bytes) -> set[str]:
    if recipe(name):
        key = Theme.from_json(content).image
        return {"studio/themes/images/" + key} if key else set()
    if name not in CATALOGUES:
        return set()
    try:
        doc = json.loads(content)
        if not isinstance(doc, dict) or doc.get("version") != 1:
            raise ValueError("Unsupported sleeve catalogue")
        if not isinstance(doc.get("entries"), dict) or len(doc["entries"]) > MAX_FILES:
            raise ValueError("Invalid or oversized sleeve catalogue")
        state = Catalog.load(doc)
    except (KeyError, TypeError, AttributeError, RecursionError, OverflowError) as exc:
        raise ValueError("Invalid sleeve catalogue") from exc
    paths = {"studio/sleeves/images/" + e.image + ".png" for e in state.entries.values()}
    paths.update(
        "studio/sleeves/originals/" + e.original for e in state.entries.values() if e.original
    )
    return paths


def retained_recipes(install: Installation, assets: set[str], cancel: Cancel) -> Iterator[str]:
    if any(name.startswith("studio/sleeves/") for name in assets):
        yield from (name for name in CATALOGUES if install.target(name).is_file())
    if any(name.startswith("studio/themes/") for name in assets):
        count = 0
        for prefix in ("studio/themes", "studio/themes/removed"):
            folder = install.target(prefix)
            if folder.exists():
                for path in folder.iterdir():
                    check(cancel)
                    if THEME_ID.fullmatch(path.name) or LEGACY_THEME.fullmatch(path.name):
                        count += 1
                        if count > MAX_FILES:
                            raise ValueError("Too many saved themes to check for undo")
                        yield f"{prefix}/{path.name}"


def retained_referrer(
    install: Installation, assets: set[str], reverting: set[str], cancel: Cancel
) -> str | None:
    for name in retained_recipes(install, assets, cancel):
        if name not in reverting:
            content = read_small(install.target(name), size_limit(name))
            if assets.intersection(references(name, content)):
                return name
    return None


def alternate_theme(name: str, number: int) -> str:
    # Replanning must reproduce the preview. Reserve the last four digits for collisions.
    stem = hashlib.sha256(name.encode("ascii")).hexdigest()[:28]
    return f"{stem}{number:04x}.json"


def is_theme_copy(source: str, target: str) -> bool:
    if not recipe(source) or not THEME_ID.fullmatch(PurePosixPath(target).name):
        return False
    original, retained = PurePosixPath(source), PurePosixPath(target)
    number = int(retained.stem[-4:], 16)
    return (
        original.parent == retained.parent
        and 1 <= number <= 10000
        and retained.name == alternate_theme(original.name, number)
    )

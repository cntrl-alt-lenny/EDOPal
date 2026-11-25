"""Read gallery artwork through the pack service without installing a pack."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from edopal.core.packs import PackInfo, PackService

SCENES = ("menu", "deck", "duel")


def _cached_image(root: Path | None, pack: PackInfo, scene: str) -> Path | None:
    if root is None:
        return None
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        name = manifest.get("image") if pack.kind == "cardback" else manifest.get("previews", {}).get(scene)
        if isinstance(name, str):
            path = (root / name).resolve()
            if path.is_relative_to(root.resolve()) and path.is_file():
                return path
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


_LOOKUP = object()


def artwork(packs: PackService, pack: PackInfo, scene: str = "tile", root=_LOOKUP) -> Path | None:
    if root is _LOOKUP:
        root = packs.cached(pack.id)
    if cached := _cached_image(root, pack, scene):
        return cached
    if scene == "tile":
        return packs.thumbnail(pack)
    previews = pack.meta.get("previews", ())
    if isinstance(previews, dict):
        url = previews.get(scene)
    elif isinstance(previews, (list, tuple)) and len(previews) > SCENES.index(scene):
        url = previews[SCENES.index(scene)]
    else:
        url = None
    if not url and scene == "menu":
        url = pack.preview
    if isinstance(url, str) and url:
        # PackService.thumbnail is its lightweight, cached image transport.
        image = replace(pack, thumbnail=url)
        return packs.thumbnail(image)
    return None


def download_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size > 0:
        return f"{max(1, round(size / 1000))} KB"
    return "Download"


def card_label(pack: PackInfo) -> str:
    label = str(pack.meta.get("label") or pack.name)
    era = str(pack.meta.get("era") or "")
    return f"{label} ({era})" if era and era not in label else label

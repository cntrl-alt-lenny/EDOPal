"""Resolve artwork in verified packs and read the selected game's card faces in jobs."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image

from .hero_sets import PRINT_SETS
from .http_cache import SourceUnavailable


@dataclass
class FormatArt:
    heroes: dict = field(default_factory=dict)
    tiles: dict = field(default_factory=dict)
    captions: dict = field(default_factory=dict)
    archetypes: dict = field(default_factory=dict)


def _pack(ctx, packs, pack_id):
    try:
        entry = next((p for p in packs.catalog("art") if p.id == pack_id), None)
        if entry:
            return packs.ensure(entry, cancel=lambda: ctx.cancelled)
    except (OSError, RuntimeError, ValueError):
        pass
    return packs.cached(pack_id)


def _image(source, bounds, crop=False):
    if not source:
        return b""
    with suppress(OSError, ValueError, Image.DecompressionBombError), Image.open(source) as img:
        if img.width * img.height > 24_000_000:
            return b""
        if crop:
            w, h = img.size
            img = img.crop((int(w * 0.135), int(h * 0.201), int(w * 0.862), int(h * 0.681)))
        img.thumbnail(bounds, Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=92)
        return buf.getvalue()
    return b""


def _face(install, code):
    if install and code:
        for ext in ("jpg", "png", "webp"):
            path = install.path("pics", f"{int(code)}.{ext}")
            if path.is_file():
                return path
    return None


def load_art(ctx, packs, rows, install):
    art = FormatArt()
    folder = _pack(ctx, packs, "art-format-heroes")
    pictured = json.loads((folder / "manifest.json").read_bytes()).get("cards", {}) if folder else {}
    singles = {}
    archetypes = _pack(ctx, packs, "art-archetypes")
    if archetypes:
        for row in json.loads((archetypes / "archetypes.json").read_bytes()):
            slug = re.sub("[^a-z0-9]+", "-", row["name"].lower()).strip("-")
            art.archetypes[row["name"].casefold()] = tuple(
                archetypes / kind / (slug + ".jpg") for kind in ("thumbs", "wide")
            )
            if len(row["cards"]) == 1:
                singles[row["cards"][0]] = art.archetypes[row["name"].casefold()][1]
    for row in rows:
        if ctx.cancelled:
            break
        slug, code = row["id"], row.get("hero_card_id")
        cards = row.get("defining_cards") or []
        photo = folder / (slug + ".jpg") if slug in pictured else None
        code = pictured.get(slug, code)
        game_face = _face(install, code)
        tile_face = game_face
        tile_photo = photo
        if slug == "goat":
            scapegoat = next((c for c in cards if c["name"] == "Scapegoat"), {})
            tile_face = _face(install, scapegoat.get("id"))
            tile_photo = singles.get(73915051, photo)
        # Catalog cells are near-square. Prefer the uncropped illustration
        # when the game has it, and keep the pack panorama for the banner.
        art.tiles[slug] = _image(tile_face or tile_photo, (280, 180), crop=bool(tile_face))
        card = next((c for c in cards if c["id"] == code), {})
        if slug == "hat":
            myrmeleo = _face(install, 91812341)
            if myrmeleo or 91812341 in singles:
                game_face = myrmeleo
                photo = None if myrmeleo else singles[91812341]
                card = {"id": 91812341, "name": "Traptrix Myrmeleo"}
        art.heroes[slug] = photo or _image(game_face, (1350, 900), crop=True)
        art.captions[slug] = (card.get("name", ""), PRINT_SETS.get(card.get("id"), ""))
    return art


def load_faces(ctx, codes, install, client=None, temporary=None):
    faces = {}
    for code in dict.fromkeys(codes):
        if ctx.cancelled:
            break
        path = _face(install, code)
        if data := _image(path, (300, 438)):
            faces[code] = (data, path)
        elif client and code:
            try:
                data = _image(BytesIO(client.card_face(code)), (740, 1080))
            except SourceUnavailable:
                break
            except (ValueError, OSError):
                continue
            if data:
                # Downloaded faces live with this preview, outside the game folder.
                path = None
                if temporary is not None:
                    with suppress(OSError), NamedTemporaryFile(dir=temporary.name, suffix=".jpg", delete=False) as out:
                        out.write(data)
                        path = Path(out.name)
                faces[code] = (data, path)
    return faces

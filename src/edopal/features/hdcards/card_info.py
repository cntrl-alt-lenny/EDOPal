"""Read the selected banner card and its installed print without touching game files."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile

from .catalog import Card, Catalog, card_sources
from .coverage import Asset, coverage


def type_line(kind: int) -> str:
    if kind & 1:
        primary = "Monster"
        subtypes = (
            (0x10, "Normal"),
            (0x40, "Fusion"),
            (0x80, "Ritual"),
            (0x2000, "Synchro"),
            (0x800000, "Xyz"),
            (0x4000000, "Link"),
            (0x1000000, "Pendulum"),
            (0x2000000, "Special Summon"),
            (0x1000, "Tuner"),
            (0x200000, "Flip"),
            (0x200, "Spirit"),
            (0x400, "Union"),
            (0x800, "Gemini"),
            (0x400000, "Toon"),
            (0x20, "Effect"),
        )
    elif kind & 2:
        primary = "Spell"
        subtypes = (
            (0x80, "Ritual"),
            (0x10000, "Quick-Play"),
            (0x20000, "Continuous"),
            (0x40000, "Equip"),
            (0x80000, "Field"),
        )
    elif kind & 4:
        primary = "Trap"
        subtypes = ((0x20000, "Continuous"), (0x100000, "Counter"))
    else:
        return ""
    types = [name for bit, name in subtypes if kind & bit]
    if not types and primary != "Monster":
        types.append("Normal")
    return " · ".join([primary, *types])


def _lookup(paths, root, code):
    for path in paths:
        try:
            if not path.resolve().is_relative_to(root) or not path.is_file():
                continue
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.execute("PRAGMA trusted_schema = OFF")
                row = db.execute(
                    "SELECT t.name, d.type, t.desc FROM datas d JOIN texts t USING(id) WHERE d.id = ?", (code,)
                ).fetchone()
            if row and isinstance(row[0], str):
                name, kind, description = row
                return name, type_line(kind) if isinstance(kind, int) else "", description or ""
        except (OSError, sqlite3.Error):
            continue
    return None


def _image_path(asset: Asset, cache: Path, root: Path) -> Path | None:
    if asset.member is None:
        return asset.path
    # The popover takes a filesystem path. Materialize mounted artwork in the app cache.
    path = cache / "hdcards" / "card-detail" / f"{asset.digest}{Path(asset.member).suffix.lower()}"
    if path.resolve().is_relative_to(root):
        return None
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temp = Path(stream.name)
                stream.write(asset.read_bytes())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
    return path


def read_card_info(game, cache, record, ctx):
    code = record["id"]
    fallback = (record["name"], "", "Card text is unavailable in this installation.")
    if game is None:
        return (
            record["name"],
            "",
            "Select your EDOPro game folder to see card text and artwork.",
            None,
            "Game Folder Required",
        )
    root = game.root.resolve()
    target = Catalog(cards={code: Card(code, record["name"])})
    try:
        paths = card_sources(root, target)
    except (OSError, ValueError, AttributeError, TypeError):
        paths = [root / "cards.cdb", root / "expansions" / "cards.cdb"]
        target.complete = False
    metadata = _lookup((path for path in reversed(paths) if path.suffix.lower() == ".cdb"), root, code) or fallback
    art = coverage(game, target, cancelled=lambda: ctx.cancelled, verify_faces=True)
    asset = art.faces.get(code)
    if asset is None:
        return *metadata, None, "Not Installed" if art.complete else "Unavailable"
    status = "HD Installed" if asset.is_hd else "Standard Resolution Installed"
    try:
        path = _image_path(asset, cache, root)
    except (OSError, BadZipFile):
        path = None
    return *metadata, path, status

"""Read historical profiles and verified whitelist bytes from the shared pack service."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from uuid import uuid4

from edopal.core.fakes import LocalPacks
from edopal.core.jobs import JobContext
from edopal.core.packs import PackInfo, PackService

from .builder.profiles import profiles
from .lflist import MAX_LIST, parse_list

LEGACY_IDS = {"goat", "edison", "hat", "tengu", "wind-up", "cat", "trooper", "chaos"}


@dataclass(frozen=True)
class Whitelist:
    profile: dict
    data: bytes
    limits: dict[int, int]


def load_manifest(folder: Path) -> dict:
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if manifest.get("schema") != 1 or manifest.get("id") != "formats" or manifest.get("kind") != "format":
        raise ValueError("The formats pack has an unrecognized manifest.")
    rows = manifest["formats"]
    ids = {row["id"] for row in rows}
    if not rows or len(ids) != len(rows) or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in ids):
        raise ValueError("The formats pack contains an invalid or duplicate format.")
    if ids != LEGACY_IDS and not ids.issuperset(row["id"] for row in profiles()):
        raise ValueError("The formats pack is missing one or more profiles.")
    return manifest


def load_list(folder: Path, slug: str, manifest: dict | None = None) -> Whitelist:
    if manifest is None:
        manifest = load_manifest(folder)
    profile = next((row for row in manifest["formats"] if row["id"] == slug), None)
    if profile is None:
        raise ValueError("This format needs the full formats pack. Connect to update the cached banlists.")
    if profile.get("status", "historical") != "historical":
        raise ValueError(f"{profile['name']} has no downloadable historical whitelist.")
    rel = profile["lflist"]
    path = folder / rel
    if path.resolve().parent != (folder.resolve() / "lflists") or path.is_symlink() or path.stat().st_size > MAX_LIST:
        raise ValueError("Invalid banlist file in the formats pack.")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != manifest["files"].get(rel) or sha != profile["sha256"]:
        raise ValueError("The cached banlist failed its integrity check. Download the formats pack again.")
    section = parse_list(data)
    if section.name != profile["display"] or not section.whitelist:
        raise ValueError("The formats pack contains an incomplete historical whitelist.")
    return Whitelist(profile, data, section.limits)


class FormatPack:
    def __init__(self, packs: PackService):
        self.packs = packs
        self._lock = Lock()

    def _ensure(self, ctx):
        pack = next((p for p in self.packs.catalog("format") if p.id == "formats"), None)
        if pack is None and isinstance(self.packs, LocalPacks):
            # A developer checkout can exist before its aggregate catalog is generated.
            pack = PackInfo("formats", "format", "Classic formats", "local", "formats", 0, "")
        if pack is None:
            if cached := self.packs.cached("formats"):
                return cached
            raise ValueError("The formats pack is unavailable. Connect to download the historical banlists.")
        if isinstance(self.packs, LocalPacks) and (old := self.packs.cached("formats")):
            if {row["id"] for row in load_manifest(old)["formats"]} == LEGACY_IDS:
                # LocalPacks reuses its first copy forever. Check the checkout before
                # evicting an older eight-format cache, so it still works offline.
                probe = replace(pack, id=f"formats-upgrade-{uuid4().hex}")
                try:
                    staged = self.packs.ensure(probe)
                    if len(load_manifest(staged)["formats"]) > len(LEGACY_IDS):
                        self.packs.remove(pack.id)
                finally:
                    self.packs.remove(probe.id)
        return self.packs.ensure(
            pack, lambda done, total: ctx.progress(done, total, "Fetching banlists"), cancel=lambda: ctx.cancelled
        )

    def profiles(self, ctx: JobContext):
        # Resolve a catalog update once per visit. Stock metadata also lets a first-time
        # offline user copy a recipe or navigate previously cached tournament pages.
        with self._lock:
            try:
                folder = self._ensure(ctx)
            except (OSError, ValueError, RuntimeError):
                folder = self.packs.cached("formats")
            rows = load_manifest(folder)["formats"] if folder else []
            if len(rows) <= len(LEGACY_IDS):
                rows = profiles()
            if folder and (index := folder / "profiles/index.json").is_file():
                metadata = {item["id"]: item for item in json.loads(index.read_bytes())}
                for row in rows:
                    info = metadata.get(row["id"], {})
                    row.update(mechanics=info.get("mechanics", {}), hero_card_id=info.get("hero_card_id"))
                    path = index.parent / (row["id"] + ".json")
                    if path.is_file():
                        overview = json.loads(path.read_bytes())
                        for key in ("description", "defining_cards"):
                            row[key] = overview.get(key)
            for row in rows:
                if not row.get("mechanics"):
                    # The bundled host recipes remain browsable on a first offline visit.
                    options = row.get("host", {}).get("options", ())
                    row["mechanics"] = {m.lower(): f"No {m}" not in options for m in ("Synchro", "Xyz", "Pendulum", "Link")}
                    if row["era"] in ("DM", "GX"):
                        row["mechanics"]["synchro"] = False
                    if row["id"] == "genesys":
                        row["mechanics"].update(link=False, pendulum=False)
            return rows

    def whitelist(self, slug: str, ctx: JobContext) -> Whitelist:
        with self._lock:
            folder = self.packs.cached("formats") or self._ensure(ctx)
            return load_list(folder, slug)

    def historical_lists(self, ctx):
        with self._lock:
            folder = self.packs.cached("formats") or self._ensure(ctx)
            manifest = load_manifest(folder)
            if len(manifest["formats"]) <= len(LEGACY_IDS):
                raise ValueError("Connect to download the full formats pack before installing all banlists.")
            lists = []
            for row in manifest["formats"]:
                if ctx.cancelled:
                    raise ValueError("Banlist installation cancelled.")
                if row.get("status", "historical") == "historical":
                    lists.append(load_list(folder, row["id"], manifest))
            return lists

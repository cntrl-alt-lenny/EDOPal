"""Read the card sources mounted by EDOPro and track additions per installation."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import CancelledError
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import BadZipFile, ZipFile

from edopal.core.install import Install
from edopal.core.settings import Settings

if TYPE_CHECKING:
    from .coverage import Coverage

SCRIPT_TYPES = {0, 0x4000, 0x10004000}


@dataclass(frozen=True)
class Card:
    id: int
    name: str
    type: int | None = None
    alias: int = 0
    rush: bool = False
    unofficial: bool = False


def file_stamp(path: Path) -> tuple[int, ...] | None:
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except FileNotFoundError:
        return None


@dataclass
class Catalog:
    cards: dict[int, Card] = field(default_factory=dict)
    sources: dict[str, set[int]] = field(default_factory=dict)
    complete: bool = True
    official: dict[str, list[int]] = field(default_factory=dict)
    fields: set[int] = field(default_factory=set)
    repo_pics: list[Path] = field(default_factory=list)
    archives: list[Path] = field(default_factory=list)
    _stamps: dict[Path, tuple[int, ...] | None] = field(default_factory=dict, repr=False)

    def stable(self):
        try:
            return self.complete and all(file_stamp(p) == stamp for p, stamp in self._stamps.items())
        except OSError:
            return False


def _entries(directory: Path, depth: int, root: Path, cat: Catalog):
    if not directory.resolve().is_relative_to(root):
        cat.complete = False
        return
    cat._stamps[directory] = file_stamp(directory)
    if cat._stamps[directory] is None:
        return
    for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            continue
        if path.is_dir():
            if depth and not path.is_symlink():
                yield from _entries(path, depth - 1, root, cat)
        elif path.is_file():
            yield path


def _repo_sources(root: Path, cat: Catalog):
    seen = set()
    for filename in ("user_configs.json", "configs.json"):
        path = root / "config" / filename
        if not path.resolve().is_relative_to(root):
            cat.complete = False
            continue
        cat._stamps[path] = file_stamp(path)
        if cat._stamps[path] is None:
            continue
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        for repo in config.get("repos", []):
            name = repo.get("repo_name") or Path(repo.get("url", "")).name
            folder = (root / (repo.get("repo_path") or f"repositories/{name}")).resolve()
            if folder in seen or not folder.is_relative_to(root):
                continue
            seen.add(folder)
            if repo.get("should_read") is False or repo.get("is_language"):
                continue
            data = (folder / repo.get("data_path", "")).resolve()
            pics = (folder / repo.get("pics_path", "pics")).resolve()
            if not (data.is_relative_to(root) and pics.is_relative_to(root)):
                cat.complete = False
                continue
            cat.repo_pics.insert(0, pics)
            if not folder.is_dir() or (repo.get("data_path") and not data.is_dir()):
                cat.complete = False
                continue
            for db in _entries(data, 0, root, cat):
                if db.suffix.lower() == ".cdb":
                    yield db


def _rows(db, name):
    db.execute("PRAGMA query_only = ON")
    db.execute("PRAGMA trusted_schema = OFF")
    columns = {col[1] for col in db.execute("PRAGMA table_info(datas)")}
    kind = "d.type" if "type" in columns else "NULL"
    alias = "d.alias" if "alias" in columns else "0"
    rush = "rush" in Path(name).name.casefold()
    unofficial = "unofficial" in Path(name).name.casefold()
    return {
        cid: Card(cid, title, typ if isinstance(typ, int) else None, alt if isinstance(alt, int) else 0, rush, unofficial)
        for cid, title, typ, alt in db.execute(f"SELECT d.id,t.name,{kind},{alias} FROM datas d JOIN texts t USING(id)")
        if type(cid) is int and 0 < cid <= 0xFFFFFFFF and isinstance(title, str)
    }


def read_catalog(game: Install, *, cancelled: Callable[[], bool] = lambda: False) -> Catalog:
    root = game.root.resolve()
    cat = Catalog()

    def add(rows, key):
        cat.cards.update(rows)
        cat.sources[key] = {cid for cid, card in rows.items() if card.type not in SCRIPT_TYPES}

    try:
        base = root / "cards.cdb"
        cat._stamps[base] = file_stamp(base)
        loose = [base] if cat._stamps[base] and base.resolve().is_relative_to(root) else []
        expansions = root / "expansions"
        for path in _entries(expansions, 2, root, cat):
            if path.suffix.lower() == ".cdb":
                loose.append(path)
            elif path.parent == expansions and path.suffix.lower() == ".zip":
                cat.archives.append(path)
        loose.sort(key=lambda p: (p != base, p.relative_to(root).as_posix().casefold()))
        repos = list(_repo_sources(root, cat))
        # Stable definitions take precedence over provisional DeltaBagooska rows.
        provisional = [
            p
            for p in repos
            if p.parent.name == "delta-bagooska"
            and p.name.startswith(("prerelease-", "release-"))
            and not p.name.endswith(".delta.cdb")
        ]
        for path in dict.fromkeys([*provisional, *loose, *cat.archives, *repos]):
            if cancelled():
                raise CancelledError
            try:
                key = path.relative_to(root).as_posix()
                stamp = cat._stamps[path] = file_stamp(path)
                if stamp is None or stamp[1] == 0:
                    cat.complete &= path == base and stamp is not None
                    continue
                if path in cat.archives:
                    with ZipFile(path) as z:
                        for member in sorted(z.infolist(), key=lambda m: m.filename.casefold()):
                            if not member.filename.lower().endswith(".cdb") or member.filename.count("/") > 3:
                                continue
                            if member.file_size > 128 * 1024**2:
                                raise ValueError("Oversized card database in expansion archive")
                            with closing(sqlite3.connect(":memory:")) as db:
                                db.deserialize(z.read(member))
                                add(_rows(db, member.filename), f"{key}!{member.filename}")
                else:
                    # Immutable read-only avoids creating -shm or journal files beside the game DB.
                    # A pending journal/WAL may contain newer rows; wait for a clean scan.
                    for suffix in ("-wal", "-journal"):
                        sidecar = path.with_name(path.name + suffix)
                        cat._stamps[sidecar] = file_stamp(sidecar)
                        if cat._stamps[sidecar] and cat._stamps[sidecar][1]:
                            cat.complete = False
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                        add(_rows(db, path.name), key)
            except (OSError, ValueError, RuntimeError, sqlite3.Error, BadZipFile):
                cat.complete = False
    except (OSError, ValueError, AttributeError, TypeError):
        cat.complete = False
    cat.cards = {cid: card for cid, card in cat.cards.items() if card.type not in SCRIPT_TYPES}
    for cid in sorted(cat.cards):
        card = cat.cards[cid]
        if cid < 100_000_000:
            cat.official.setdefault(card.name, []).append(cid)
        if (card.type or 0) & 0x80002 == 0x80002:
            cat.fields.add(cid)
    cat.complete = bool(cat.cards) and cat.stable()
    return cat


def deck_ids(game: Install) -> set[int]:
    folder = game.path("deck").resolve()
    result = set()
    if folder.is_relative_to(game.root.resolve()):
        for path in folder.rglob("*"):
            if path.suffix.casefold() == ".ydk" and path.is_file() and path.resolve().is_relative_to(folder):
                with path.open(encoding="utf-8-sig", errors="replace") as deck:
                    for line in deck:
                        code = line.strip()
                        if 0 < len(code) <= 10 and code.isascii() and code.isdecimal() and 0 < int(code) <= 0xFFFFFFFF:
                            result.add(int(code))
    return result


@dataclass(frozen=True)
class Notice:
    new: set[int] = field(default_factory=set)
    needs: set[int] = field(default_factory=set)


def check_updates(settings: Settings, game: Install, catalog: Catalog, coverage: Coverage) -> Notice:
    key = "snapshot:" + os.path.normcase(str(game.root.resolve()))
    previous = settings.get("hdcards", key, {})
    pending = set(previous.get("new", ()))
    prior_needs = set(previous.get("needs", ()))
    if not (catalog.cards and catalog.stable() and coverage.complete):
        return Notice(pending, prior_needs)
    current = set(catalog.cards)
    additions = current - set(previous["ids"]) if "ids" in previous else set()
    missing = (current - coverage.hd_faces) | (catalog.fields - coverage.fields.keys())
    # Keep the new-card cohort (including cards which arrived with art) until its needs are fulfilled.
    pending = ((pending & current) - (prior_needs - missing)) | additions
    needs = pending & missing
    if not needs and not additions:
        pending.clear()
    record = {"ids": sorted(current), "new": sorted(pending), "needs": sorted(needs), "version": game.version}
    if record != previous:
        settings.set("hdcards", key, record)
    return Notice(pending, needs)

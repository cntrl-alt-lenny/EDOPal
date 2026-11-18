"""Translate regional Format Library identities to the pinned simulator's playable printings."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from card_databases import get_db_files

HISTORICAL = re.compile(r" \((?:GOAT|Pre-Errata)\)$", re.I)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Printing(NamedTuple):
    name: str
    alias: int
    scope: int
    kind: int


Database = dict[int, Printing]


def database(game: Path) -> tuple[Database, list[str]]:
    db: Database = {}
    hashes = []
    for filename in get_db_files(str(game.resolve())):
        path = Path(filename)
        hashes.append(digest(path.read_bytes()))
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as con:
            for code, *fields in con.execute(
                "SELECT id,name,alias,ot,type FROM datas JOIN texts USING(id)"
            ):
                db[code] = Printing(*fields)
    if not db:
        raise ValueError("Empty card database snapshot")
    return db, hashes


def normalize(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold().replace("’", "'").strip()


def effective(entries: dict[int, int], code: int, alias: int) -> int | None:
    if code in entries:
        return entries[code]
    if alias and min((code - alias) & 0xFFFFFFFF, (alias - code) & 0xFFFFFFFF) < 10:
        return entries.get(alias)
    return None


def list_hash(rows: list[tuple[int, int]]) -> str:
    def rotate(value: int, n: int) -> int:
        return ((value << n) | (value >> (32 - n))) & 0xFFFFFFFF

    value = 0x7DFCEE6A
    for code, cap in rows:
        value ^= rotate(code, 18) ^ rotate(code, 27 + cap)
    return f"0x{value:08x}"


class Index:
    def __init__(self, cards: list[dict], db: Database) -> None:
        self.cards = {c["id"]: c for c in cards}
        self.families: dict[int, tuple[int, set[int]]] = {}
        self.inverse: dict[int, set[int]] = defaultdict(set)
        self.excluded: dict[int, str] = {}
        official = {code for code, p in db.items() if p.scope in (1, 2, 3) and not p.kind & 0x4000}
        names: dict[str, set[int]] = defaultdict(set)
        provisional: dict[str, set[int]] = defaultdict(set)
        for code, p in db.items():
            if code in official:
                names[normalize(p.name)].add(code)
            elif p.scope & 0x100 and p.scope & 3 and not p.kind & 0x4000:
                provisional[normalize(p.name)].add(code)

        for fid, card in self.cards.items():
            name = normalize(card["name"])
            fields = [
                int(value)
                for key in ("konamiCode", "ypdId", "artworkId")
                if (value := str(card.get(key) or "")).isdigit() and int(value)
            ]
            group = names[name]
            matches = [v for v in fields if v in group]
            if matches:
                root = matches[0]
            elif group and len({(db[v].alias or v, db[v].kind) for v in group}) == 1:
                root = min(group, key=lambda v: (bool(db[v].alias), v))
            elif not group and len(set(fields) & official) == 1:
                (root,) = set(fields) & official
            elif not group and len(provisional[name]) == 1:
                (root,) = provisional[name]
            else:
                reason = "No unique playable printing"
                if any(v in db and db[v].kind & 0x4000 for v in fields):
                    reason = "Token"
                self.excluded[fid] = reason
                continue
            local = db[root]
            alias = local.alias
            # Copy families can include different physical cards, including the Normal
            # and Ritual Black Luster Soldiers. Preserve both name and database type.
            if (
                alias in official
                and normalize(db[alias].name) == normalize(local.name)
                and db[alias].kind == local.kind
            ):
                root = alias
            family = {
                v
                for v in names[normalize(local.name)]
                if db[v].alias == root and db[v].kind == local.kind
            } | {root}
            self.families[fid] = root, family
            for code in family:
                self.inverse[code].add(fid)


@dataclass
class Pool:
    entries: dict[int, int] = field(default_factory=dict)
    targets: dict[int, set[int]] = field(default_factory=dict)
    exclusions: list[dict] = field(default_factory=list)
    historical: dict[int, list[int]] = field(default_factory=dict)
    fallback: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)


def history_at(pin: dict, when: str) -> dict[int, str | list[int]]:
    if when not in pin["dates"]:
        raise ValueError(f"Refresh the reviewed printing decisions for {when}")
    return {
        int(root): changes[max(d for d in changes if d <= when)]
        for root, changes in pin["cards"].items()
    }


def build_pool(
    row: dict,
    members: set[int],
    bans: dict[int, int],
    index: Index,
    db: Database,
    decisions: dict[int, str | list[int]],
    stock: dict[int, int],
    scripts: set[int],
) -> Pool:
    result = Pool()
    name = row["name"]
    goat: dict[int, set[int]] = defaultdict(set)
    if name == "Goat":
        for code in stock:
            if HISTORICAL.search(db[code].name):
                goat[db[code].alias].add(code)
    for fid in sorted(bans.keys() - members):
        if bans[fid] < 3:
            result.notes.append(
                f"{index.cards[fid]['name']} appears on the banlist but falls outside this region’s card pool."
            )
    for fid in sorted(members):
        family = index.families.get(fid)
        reason = index.excluded.get(fid)
        if family:
            root, ids = family
            if name == "Goat" and root in (48642904, 68811206):
                reason = "Absent from the official Goat list"
            if name == "Edison" and root == 68811206:
                reason = "Excluded by the reviewed Edison pool"
        if reason:
            result.exclusions.append(dict(id=fid, name=index.cards[fid]["name"], reason=reason))
            continue
        choices = goat.get(root) if name == "Goat" else decisions.get(root)
        if name == "Edison" and root == 96782886:
            choices = [96782896]
        if isinstance(choices, (list, set)):
            if all(i in db and db[i].alias == root and i in scripts for i in choices):
                ids = set(choices)
                result.historical[fid] = sorted(ids)
            else:
                result.fallback["unavailable"] += 1
        elif choices and choices != "modern":
            result.fallback[choices] += 1
        cap = bans.get(fid, 3)
        result.targets[fid] = ids
        for code in ids:
            if result.entries.setdefault(code, cap) != cap:
                raise ValueError(f"Conflicting limits for {name}, card {code}")
    return result

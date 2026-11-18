"""Card metadata and installed evidence for retired passcodes."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .sources import Sources, bounded_text

TYPES = {
    1: "Monster",
    2: "Spell",
    4: "Trap",
    0x10: "Normal",
    0x20: "Effect",
    0x40: "Fusion",
    0x80: "Ritual",
    0x100: "Trap Monster",
    0x200: "Spirit",
    0x400: "Union",
    0x800: "Gemini",
    0x1000: "Tuner",
    0x2000: "Synchro",
    0x4000: "Token",
    0x8000: "Maximum",
    0x10000: "Quick-Play",
    0x20000: "Continuous",
    0x40000: "Equip",
    0x80000: "Field",
    0x100000: "Counter",
    0x200000: "Flip",
    0x400000: "Toon",
    0x800000: "Xyz",
    0x1000000: "Pendulum",
    0x2000000: "Special Summon",
    0x4000000: "Link",
    0x8000000: "Skill",
    0x10000000: "Action",
}
ATTRIBUTES = ("EARTH", "WATER", "FIRE", "WIND", "LIGHT", "DARK", "DIVINE")
RACES = (
    "Warrior",
    "Spellcaster",
    "Fairy",
    "Fiend",
    "Zombie",
    "Machine",
    "Aqua",
    "Pyro",
    "Rock",
    "Winged Beast",
    "Plant",
    "Insect",
    "Thunder",
    "Dragon",
    "Beast",
    "Beast-Warrior",
    "Dinosaur",
    "Fish",
    "Sea Serpent",
    "Reptile",
    "Psychic",
    "Divine-Beast",
    "Creator God",
    "Wyrm",
    "Cyberse",
    "Illusion",
    "Cyborg",
    "Magical Knight",
    "High Dragon",
    "Omega Psychic",
    "Celestial Warrior",
    "Galaxy",
)


def passcode(value: object) -> bool:
    return type(value) is int and 0 < value <= 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class Card:
    id: int
    name: str
    alias: int | None
    scope: int | None
    type: int
    type_line: str
    attribute: str
    rating_kind: str
    rating: int | None
    archetypes: tuple[str, ...]

    @property
    def kind(self) -> str:
        return {1: "monster", 2: "spell", 4: "trap"}.get(self.type & 7, "other")


@dataclass
class Catalog:
    cards: dict[int, Card]
    successors: dict[int, set[int]]
    conflicts: set[int]
    issues: tuple[str, ...]


def _load_cards(path: Path, labels: dict[int, str]) -> dict[int, Card]:
    # Immutable also prevents SQLite from creating journal/shm sidecars on live installations.
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
        raise ValueError("Database has a pending journal; rescan after EDOPro closes")
    cards = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        columns = {r[1] for r in db.execute("PRAGMA table_info(datas)")}
        fields = ("alias", "ot", "type", "race", "attribute", "level", "setcode")
        selected = ", ".join("datas." + k if k in columns else "NULL" for k in fields)
        query = f"SELECT datas.id, texts.name, {selected} FROM datas JOIN texts USING(id)"
        for code, name, alias, scope, flags, race, attr, level, sets in db.execute(query):
            if not passcode(code) or not isinstance(name, str):
                continue
            flags = flags if isinstance(flags, int) else 0
            types = [v for k, v in TYPES.items() if flags & k]
            if flags & 1 and isinstance(race, int):
                types[:0] = [v for i, v in enumerate(RACES) if race & (1 << i)]
            attribute = " / ".join(
                v for i, v in enumerate(ATTRIBUTES) if isinstance(attr, int) and attr & (1 << i)
            )
            kind = "Link" if flags & 0x4000000 else "Rank" if flags & 0x800000 else "Level"
            rating = None
            if flags & 1 and isinstance(level, int):
                rating = level & 0xFF
                if level < 0:
                    rating = -rating
            codes = (
                [(sets >> shift) & 0xFFFF for shift in (0, 16, 32, 48)]
                if isinstance(sets, int)
                else []
            )
            cards[code] = Card(
                code,
                name,
                alias if alias == 0 or passcode(alias) else None,
                scope if isinstance(scope, int) else None,
                flags,
                " / ".join(types),
                attribute,
                kind if flags & 1 else "",
                rating,
                tuple(labels.get(c, f"0x{c:04x}") for c in codes if c),
            )
    return cards


def load_catalog(sources: Sources) -> Catalog:
    issues = list(sources.issues)
    labels: dict[int, str] = {}
    for path in sources.strings:
        try:
            for line in bounded_text(path, 4 << 20).splitlines():
                if line.startswith("!setname "):
                    _, key, label = line.split(maxsplit=2)
                    labels[int(key, 16)] = label
        except (OSError, ValueError) as exc:
            issues.append(f"{path}: {exc}")

    cards: dict[int, Card] = {}
    conflicts: set[int] = set()
    for path in sources.databases:
        try:
            if path.stat().st_size == 0:
                continue
            for code, card in _load_cards(path, labels).items():
                previous = cards.get(code)
                if previous and (previous.name, previous.alias, previous.scope, previous.type) != (
                    card.name,
                    card.alias,
                    card.scope,
                    card.type,
                ):
                    conflicts.add(code)
                cards[code] = card
        except (OSError, ValueError, sqlite3.Error, TypeError) as exc:
            issues.append(f"{path}: {exc}")

    successors: defaultdict[int, set[int]] = defaultdict(set)
    for path in sources.mappings:
        try:
            data = json.loads(bounded_text(path, 4 << 20))
            if not isinstance(data, dict) or not isinstance(data.get("mappings"), list):
                raise ValueError("Expected a mappings array")
            for pair in data["mappings"]:
                if not isinstance(pair, list) or len(pair) != 2 or not all(map(passcode, pair)):
                    raise ValueError("Invalid passcode pair")
                successors[pair[0]].add(pair[1])
        except (ValueError, OSError, RecursionError) as exc:
            issues.append(f"{path}: {exc}")
    # A mapping is an explicit successor. Reverse aliases are weaker evidence, used
    # only when no mapping exists; multiple possible prints require manual review.
    mapped = set(successors)
    for card in cards.values():
        if card.alias and card.alias not in cards and card.alias not in mapped:
            successors[card.alias].add(card.id)
    return Catalog(cards, dict(successors), conflicts, tuple(issues))

"""Match public preview entries to local artwork and card types without downloading it."""

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from card_databases import get_db_files
from tournament_decks import DeckPreview

GROUPS = ("Main", "Extra", "Side")
CARD_TYPES = {1: "Monsters", 2: "Spells", 4: "Traps"}


@dataclass(frozen=True)
class PreviewCard:
    name: str
    copies: int
    artwork: Path | None
    types: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class PreviewSection:
    cards: tuple[PreviewCard, ...]

    @property
    def count(self) -> int:
        return sum(card.copies for card in self.cards)

    @property
    def type_counts(self) -> tuple[tuple[str, int], ...]:
        totals: Counter[str] = Counter()
        for card in self.cards:
            totals.update(dict(card.types))
        return tuple(
            (kind, totals[kind]) for kind in (*CARD_TYPES.values(), "Unknown type") if totals[kind]
        )


def _local_types(game: Path, ids: set[int], cancel: Event) -> dict[int, str]:
    result: dict[int, str] = {}
    for path in get_db_files(str(game.resolve())):
        if cancel.is_set():
            break
        try:
            with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
                # Chunk large published lists for SQLite builds with a low variable limit.
                codes = sorted(ids)
                for start in range(0, len(codes), 500):
                    chunk = codes[start : start + 500]
                    params = ",".join("?" for _ in chunk)
                    for code, flags in db.execute(
                        f"SELECT id, type FROM datas WHERE id IN ({params})", chunk
                    ):
                        if isinstance(flags, int):
                            result[code] = CARD_TYPES.get(flags & 7, "Unknown type")
        except sqlite3.Error:
            continue
    return result


def _artwork(pics: Path, codes: Counter[int]) -> Path | None:
    for code in codes:
        for extension in (".jpg", ".png"):
            path = pics / f"{code}{extension}"
            if path.is_file():
                return path
    return None


def prepare_preview(game: Path, preview: DeckPreview, cancel: Event) -> tuple[PreviewSection, ...]:
    all_ids = {code for entries in preview.printings for code, _ in entries if code}
    types = _local_types(game, all_ids, cancel) if all_ids else {}
    pics = game / "pics"
    sections = []
    for index, entries in enumerate(preview.sections):
        known: dict[str, Counter[int]] = {}
        prints = preview.printings[index] if index < len(preview.printings) else ()
        for code, name in prints:
            if code:
                known.setdefault(name, Counter())[code] += 1
        cards = []
        for name, count in entries:
            if cancel.is_set():
                return ()
            codes = known.get(name, Counter())
            counts: Counter[str] = Counter()
            for code, copies in codes.items():
                counts[types.get(code, "Unknown type")] += copies
            unknown = count - counts.total()
            if unknown > 0:
                counts["Unknown type"] += unknown
            cards.append(PreviewCard(name, count, _artwork(pics, codes), tuple(counts.items())))
        sections.append(PreviewSection(tuple(cards)))
    return tuple(sections)

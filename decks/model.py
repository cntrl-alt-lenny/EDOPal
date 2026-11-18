"""Ordered deck contents and the section counts used throughout the library."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .cards import Card, Catalog


class Sections(NamedTuple):
    main: tuple[int, ...] = ()
    extra: tuple[int, ...] = ()
    side: tuple[int, ...] = ()


class Counts(NamedTuple):
    main: int = 0
    extra: int = 0
    side: int = 0


@dataclass(frozen=True)
class Preview:
    sections: Sections
    counts: Counts
    cards: dict[int, Card]
    unresolved: tuple[int, ...]
    breakdown: dict[str, int]
    copies: dict[int, Counts]


def preview(sections: Sections, catalog: Catalog) -> Preview:
    by_section = [Counter(section) for section in sections]
    codes = dict.fromkeys(code for section in sections for code in section)
    copies = {code: Counts(*(counts[code] for counts in by_section)) for code in codes}
    cards = {code: catalog.cards[code] for code in codes if code in catalog.cards}
    breakdown = dict.fromkeys(("monster", "spell", "trap", "other", "unresolved"), 0)
    for code, counts in copies.items():
        breakdown[cards[code].kind if code in cards else "unresolved"] += sum(counts)
    return Preview(
        sections,
        Counts(*map(len, sections)),
        cards,
        tuple(code for code in codes if code not in cards),
        breakdown,
        copies,
    )


@dataclass(frozen=True)
class SavedDeck:
    path: Path
    folder: Path  # Relative to the installation's deck folder.
    modified_ns: int
    preview: Preview | None
    error: str = ""
    file_id: tuple[int, int] | None = None

    @property
    def name(self) -> str:
        return self.path.stem

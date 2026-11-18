"""Copy-aware comparisons on original passcodes."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .model import SavedDeck, Sections


@dataclass(frozen=True)
class Change:
    kind: Literal["added", "removed", "count changed", "moved"]
    card_id: int
    copies: int
    source: str | None
    destination: str | None


def compare(
    before: Sections, after: Sections, *, separate_counts: bool = False
) -> tuple[Change, ...]:
    """Cancel retained copies, pair section transfers, then report net quantity changes."""
    old, new = [Counter(s) for s in before], [Counter(s) for s in after]
    changes = []
    for code in sorted(set().union(*old, *new)):
        delta = [b[code] - a[code] for a, b in zip(old, new, strict=True)]
        for i, source in enumerate(Sections._fields):
            for j, destination in enumerate(Sections._fields):
                amount = min(-delta[i], delta[j])
                if amount > 0:
                    changes.append(Change("moved", code, amount, source, destination))
                    delta[i] += amount
                    delta[j] -= amount
        for i, section in enumerate(Sections._fields):
            if delta[i] > 0:
                kind = "count changed" if old[i][code] and not separate_counts else "added"
                changes.append(Change(kind, code, delta[i], None, section))
            elif delta[i] < 0:
                kind = "count changed" if new[i][code] and not separate_counts else "removed"
                changes.append(Change(kind, code, -delta[i], section, None))
    return tuple(changes)


def _stem(name: str) -> str:
    name = re.split(r"\s+[-–—]\s+", name.casefold(), maxsplit=1)[0]
    return re.sub(
        r"(?:[\s_-]+(?:v(?:ersion)?\.?\s*\d+(?:\.\d+)*|rev\s*\d+|copy|\(\d+\)))+$", "", name
    ).strip()


def version_groups(decks: Iterable[SavedDeck]) -> tuple[tuple[SavedDeck, ...], ...]:
    """Require a shared title stem and at least 70% multiset overlap with every member."""
    candidates: defaultdict[str, list[list[tuple[SavedDeck, Counter[int]]]]] = defaultdict(list)
    for deck in sorted(decks, key=lambda d: d.path):
        if not deck.preview:
            continue
        cards = Counter({code: sum(counts) for code, counts in deck.preview.copies.items()})
        if not cards:
            continue
        groups = candidates[_stem(deck.name)]
        for group in groups:
            if all(
                (cards & other).total() >= 0.7 * max(cards.total(), other.total())
                for _, other in group
            ):
                group.append((deck, cards))
                break
        else:
            groups.append([(deck, cards)])
    return tuple(
        tuple(deck for deck, _ in group)
        for groups in candidates.values()
        for group in groups
        if len(group) > 1
    )

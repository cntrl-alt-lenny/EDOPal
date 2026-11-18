"""Section-aware exact matches and near matches across the saved library."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .compare import compare
from .library import _check_cancelled
from .model import Preview, SavedDeck, Sections

NEAR_EDITS = 3
Signature = tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class DuplicateIndex:
    groups: tuple[tuple[SavedDeck, ...], ...]
    by_path: dict[Path, int]
    marks: dict[Path, str]


def difference(before: Sections, after: Sections) -> int:
    changes = compare(before, after, separate_counts=True)
    added = sum(change.copies for change in changes if change.kind == "added")
    removed = sum(change.copies for change in changes if change.kind == "removed")
    moved = sum(change.copies for change in changes if change.kind == "moved")
    return moved + max(added, removed)


def _signature(preview: Preview) -> Signature:
    return tuple(
        (code, section, copies)
        for code, counts in sorted(preview.copies.items())
        for section, copies in enumerate(counts)
        if copies
    )


def _parts(signature: Signature) -> tuple[Signature, ...]:
    buckets: list[list[tuple[int, int, int]]] = [[] for _ in range(2 * NEAR_EDITS + 1)]
    for code, section, copies in signature:
        buckets[(code * 3 + section) % len(buckets)].append((code, section, copies))
    return tuple(map(tuple, buckets))


def _near(before: Signature, after: Signature) -> bool:
    i = j = added = removed = 0
    while i < len(before) or j < len(after):
        if j == len(after) or i < len(before) and before[i][:2] < after[j][:2]:
            removed += before[i][2]
            i += 1
        elif i == len(before) or after[j][:2] < before[i][:2]:
            added += after[j][2]
            j += 1
        else:
            delta = after[j][2] - before[i][2]
            added += max(delta, 0)
            removed += max(-delta, 0)
            i += 1
            j += 1
        # Pairing a move or a replacement uses one addition and one removal.
        if max(added, removed) > NEAR_EDITS:
            return False
    return True


def find_duplicates(decks: tuple[SavedDeck, ...], cancel: Event | None = None) -> DuplicateIndex:
    exact: defaultdict[Signature, list[SavedDeck]] = defaultdict(list)
    for deck in sorted(decks, key=lambda d: d.path):
        _check_cancelled(cancel)
        if deck.preview is not None:
            exact[_signature(deck.preview)].append(deck)
    signatures = sorted(exact, key=lambda s: sum(row[2] for row in s))
    totals = [sum(row[2] for row in s) for s in signatures]
    parents = list(range(len(signatures)))
    near: set[int] = set()
    postings: defaultdict[tuple[int, Signature], list[int]] = defaultdict(list)

    def root(index: int) -> int:
        while index != parents[index]:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, signature in enumerate(signatures):
        _check_cancelled(cancel)
        candidates: set[int] = set()
        # Three edits can touch at most six card/section keys, so one part must match.
        for part, values in enumerate(_parts(signature)):
            posted = postings[part, values]
            for previous in reversed(posted):
                if totals[index] - totals[previous] > NEAR_EDITS:
                    break
                candidates.add(previous)
            posted.append(index)
        for previous in candidates:
            _check_cancelled(cancel)
            a, b = root(index), root(previous)
            if a == b and index in near and previous in near:
                continue
            if _near(signatures[previous], signature):
                near.update((index, previous))
                parents[max(a, b)] = min(a, b)
    families: defaultdict[int, list[SavedDeck]] = defaultdict(list)
    marks: dict[Path, str] = {}
    for i, signature in enumerate(signatures):
        members = exact[signature]
        if len(members) > 1 or i in near:
            duplicate = len(members) > 1
            mark = (
                "Identical & similar"
                if duplicate and i in near
                else "Identical"
                if duplicate
                else "Similar"
            )
            families[root(i)].extend(members)
            marks.update((deck.path, mark) for deck in members)
    groups = tuple(
        sorted(
            (tuple(sorted(members, key=lambda d: d.path)) for members in families.values()),
            key=lambda group: group[0].name.casefold(),
        )
    )
    return DuplicateIndex(
        groups,
        {deck.path: i for i, group in enumerate(groups) for deck in group},
        marks,
    )

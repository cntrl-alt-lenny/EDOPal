"""Player-facing projections of a read-only deck library."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from decks import Change, Counts, Finding, Library, Preview, SavedDeck


def quantity(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def folder_name(deck: SavedDeck) -> str:
    return "Deck folder" if deck.folder == Path(".") else deck.folder.as_posix()


def changed_date(stamp: int) -> str:
    try:
        return datetime.fromtimestamp(stamp / 1e9).strftime("%d %b %Y") if stamp else "Unknown date"
    except (OSError, OverflowError, ValueError):
        return "Unknown date"


def file_problem(reason: str) -> str:
    return reason.replace("YDK section or passcode", "deck section or card number").replace(
        "Binary data in a text file", "The file does not contain a readable list of cards"
    )


def deck_title(deck: SavedDeck) -> str:
    return (deck.folder / deck.name).as_posix()


def section_counts(counts: Counts) -> str:
    return f"{counts.main} Main   ·   {counts.extra} Extra   ·   {counts.side} Side"


def card_name(preview: Preview, code: int) -> str:
    return preview.cards[code].name if code in preview.cards else f"Unknown card #{code}"


def breakdown(preview: Preview) -> str:
    return (
        "   ·   ".join(
            quantity(n, kind) if kind != "unresolved" else f"{n} unknown"
            for kind, n in preview.breakdown.items()
            if n
        )
        or "Empty deck"
    )


@dataclass(frozen=True)
class LibraryRow:
    deck: SavedDeck
    counts: str
    folder: str
    changed: str
    problem: str


def library_rows(
    lib: Library, term: str, by_card: bool, folder: Path | None, sort: int
) -> list[LibraryRow]:
    term = term.strip()
    matches = {m.deck.path: m.counts for m in lib.by_card(term)} if by_card and term else {}
    rows = []
    for deck in lib.decks:
        if folder is not None and deck.folder != folder:
            continue
        if term and (
            deck.path not in matches if by_card else term.casefold() not in deck.name.casefold()
        ):
            continue
        p = deck.preview
        counts = section_counts(matches.get(deck.path, p.counts)) if p else "Counts unavailable"
        problem = ""
        if not p:
            problem = "Unreadable"
        elif p.unresolved:
            problem = quantity(len(p.unresolved), "unknown card")
        rows.append(
            LibraryRow(deck, counts, folder_name(deck), changed_date(deck.modified_ns), problem)
        )
    rows.sort(key=lambda r: (r.deck.name.casefold(), r.folder.casefold()))
    if sort == 1:
        rows.sort(key=lambda r: r.deck.modified_ns, reverse=True)
    elif sort == 2:
        rows.sort(key=lambda r: sum(r.deck.preview.counts) if r.deck.preview else -1, reverse=True)
    return rows


def change_lines(before: Preview, after: Preview, changes: tuple[Change, ...]) -> list[str]:
    lines = []
    for change in changes:
        c = change.card_id
        name = card_name(after if c in after.cards else before, c)
        source, dest = (change.source or "").title(), (change.destination or "").title()
        if change.kind == "moved":
            detail = f"{change.copies} moved from {source} to {dest}"
        elif change.kind == "count changed":
            section = change.source or change.destination
            old = getattr(before.copies[c], section)
            new = getattr(after.copies[c], section)
            detail = f"{section.title()} changed from {old} to {new}"
        elif change.kind == "added":
            detail = f"{change.copies} added to {dest}"
        else:
            detail = f"{change.copies} removed from {source}"
        lines.append(f"{name}  ·  {detail}")
    return lines


def finding_text(finding: Finding) -> str:
    if finding.rule == "deck-file":
        return file_problem(finding.reason)
    reason = re.sub(r" \(\d+\)", "", finding.reason)
    if finding.rule == "unresolved":
        return f"The game has no card information for printed number {finding.ids[0]}."
    return (
        reason.replace("is absent from the whitelist", "is outside this list’s allowed cards")
        .replace(
            "has custom, historical or unverified card scope",
            "may have special rules for this room or era",
        )
        .replace("The alias identity of", "The link between printings of")
        .replace("is incomplete or chained", "could not be confirmed")
        .replace("different alias identities", "conflicting links between printings")
        .replace("Installed IDs with the name", "Printings named")
        .replace("need client verification", "must be checked in EDOPro")
    )


def list_names(lib: Library) -> list[str]:
    names = Counter(b.name for b in lib.banlists)
    return [
        b.name
        if names[b.name] == 1 and b.name
        else f"{b.name or 'Unnamed list'} · {b.path.relative_to(lib.game)} · line {b.line}"
        for b in lib.banlists
    ]

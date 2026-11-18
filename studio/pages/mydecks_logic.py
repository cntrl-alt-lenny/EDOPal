"""Player-facing projections of a read-only deck library."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from time import time_ns

from decks import Change, Counts, DeckIndexer, Finding, Library, Preview, SavedDeck
from decks.duplicates import DuplicateIndex, find_duplicates
from decks.history import DeckStore, Ledger
from studio.pages.decks_preview_data import PreviewCard, PreviewSection, local_artwork

TOURNAMENT_NAME = re.compile(
    r"^.+? - (?P<event>.+?) - (?:(?P<place>\d+) )?(?P<name>.+) - "
    r"(?P<pilot>.+?) \[FL\d+\](?P<variant>(?: \([^()]+\))*)$"
)


def scan_collection(
    index: DeckIndexer, store: DeckStore, cancel: Event
) -> tuple[Library, Ledger, DuplicateIndex, bool]:
    started = time_ns()
    lib = index.scan(cancel=cancel)
    duplicates = find_duplicates(lib.decks, cancel)
    try:
        ledger = store.observe(lib, started, cancel)
    except InterruptedError:
        raise
    except (OSError, ValueError, sqlite3.Error):
        return lib, Ledger({}, ()), duplicates, False
    return lib, ledger, duplicates, True


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
    return (
        reason.replace("YDK section or passcode", "deck section or card number")
        .replace("Binary data in a text file", "The file does not contain a readable list of cards")
        .replace("YDK has no main deck", "The file has no Main Deck")
    )


def deck_title(deck: SavedDeck) -> str:
    return (deck.folder / deck.name).as_posix()


def library_title(name: str) -> tuple[str, str]:
    """Show Format Library metadata separately while retaining the original file name."""
    match = TOURNAMENT_NAME.fullmatch(name)
    if match is None:
        return name, ""
    event, place, title, pilot, variant = (
        match.group(key) for key in ("event", "place", "name", "pilot", "variant")
    )
    parts = (event, f"Place {int(place)}" if place else "", pilot)
    return title + variant, " · ".join(filter(None, parts))


def section_counts(counts: Counts) -> str:
    return f"{counts.main} Main   ·   {counts.extra} Extra   ·   {counts.side} Side"


def card_name(preview: Preview, code: int) -> str:
    return preview.cards[code].name if code in preview.cards else f"Unknown card #{code}"


def saved_sections(game: Path, preview: Preview, cancel: Event) -> tuple[PreviewSection, ...]:
    pictures = game / "pics"
    sections = []
    for codes in preview.sections:
        cards: dict[str, PreviewCard] = {}
        for code, copies in Counter(codes).items():
            if cancel.is_set():
                return ()
            card = preview.cards.get(code)
            name = card_name(preview, code)
            existing = cards.get(name)
            types = Counter(dict(existing.types)) if existing else Counter()
            types[card.kind.title() if card else "Unknown type"] += copies
            artwork = (existing.artwork if existing else None) or local_artwork(pictures, (code,))
            detail = "Missing from game data"
            if card:
                rating = (
                    f"{card.rating_kind} {card.rating}"
                    if card.rating_kind and card.rating is not None
                    else ""
                )
                detail = " · ".join(filter(None, (card.type_line, rating, card.attribute.title())))
            cards[name] = PreviewCard(
                name,
                copies + (existing.copies if existing else 0),
                artwork,
                tuple(types.items()),
                existing.detail if existing else detail,
            )
        sections.append(PreviewSection(tuple(cards.values())))
    return tuple(sections)


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
    match: str = ""
    title: str = ""
    tournament: str = ""


def library_rows(
    lib: Library,
    term: str,
    by_card: bool,
    folder: Path | None,
    sort: int,
    marks: Mapping[Path, str] | None = None,
) -> list[LibraryRow]:
    term = term.strip()
    matches = {m.deck.path: m.counts for m in lib.by_card(term)} if by_card and term else {}
    rows = []
    for deck in lib.decks:
        title, tournament = library_title(deck.name)
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
            LibraryRow(
                deck,
                counts,
                folder_name(deck),
                changed_date(deck.modified_ns),
                problem,
                marks.get(deck.path, "") if marks else "",
                title,
                tournament,
            )
        )
    rows.sort(key=lambda r: (r.title.casefold(), r.folder.casefold()))
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
        return f"Card #{finding.ids[0]} is missing from this installation’s card data."
    if finding.rule == "format":
        return "Check Rush Duel or Speed Duel deck sizes, special cards and room rules in EDOPro."
    if finding.rule in {"forbidden", "limited", "semi-limited"} and (
        limit := re.fullmatch(r"(.+) reached copy (\d+), above the (.+) limit of (-?\d+)\.", reason)
    ):
        name, copies, banlist, allowed = limit.groups()
        if int(allowed) <= 0:
            return f"{name} is forbidden by the {banlist} list."
        copies_allowed = "1 copy" if allowed == "1" else f"{allowed} copies"
        return f"{name} has at least {copies} copies. The {banlist} list allows {copies_allowed}."
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
        .replace(
            "Their order affects the client check", "EDOPro applies those limits in deck order"
        )
        .replace("the room's allowed pool", "the cards allowed in the room")
        .replace("The room's allowed pool", "The cards allowed in the room")
        .replace(
            "in Extra requires a room allowing Rituals there",
            "can only go in the Extra Deck if the room allows Ritual Monsters there",
        )
        .replace("across sections", "across the Main, Extra and Side Decks")
    )


def list_names(lib: Library) -> list[str]:
    names = Counter(b.name for b in lib.banlists)
    return [
        b.name
        if names[b.name] == 1 and b.name
        else f"{b.name or 'Unnamed list'} · {b.path.relative_to(lib.game)} · line {b.line}"
        for b in lib.banlists
    ]

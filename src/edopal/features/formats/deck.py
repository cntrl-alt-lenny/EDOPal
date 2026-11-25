"""Import a published YDK against the selected game's card identities and the pack's whitelist."""

from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .http_cache import DeckError

if TYPE_CHECKING:
    from edopal.core.install import Install

    from .client import DeckPreview

SECTIONS = ("#main", "#extra", "!side")
Sections = tuple[tuple[str, ...], ...]
HISTORICAL = re.compile(r" \((?:goat|pre-errata)\)$", re.IGNORECASE)
# Historical artwork pairs verified in the archived GOAT importer.
ARTWORK = {
    504700040: 23401840,
    504700142: 83011278,
    511000824: 83555667,
    511000825: 83555666,
    511002992: 14878871,
    511003023: 80604092,
    511000822: 57728570,
    511000823: 57728571,
    504700111: 68540058,
    511006005: 68540059,
    504700179: 14878871,
}


class DeckChanged(DeckError):
    """The downloadable YDK and the previously selected preview have diverged."""


@dataclass(frozen=True)
class PreparedDeck:
    content: bytes
    changes: tuple[str, ...]
    issues: tuple[str, ...]


def read_ydk(source: str | bytes) -> Sections:
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8-sig")
        except UnicodeError as exc:
            raise DeckError("Format Library returned an unreadable YDK.") from exc
    sections: list[list[str]] = [[], [], []]
    section = -1
    for line in source.lstrip("\ufeff").replace("\r\n", "\n").removeprefix("#created\nby\n...\n").splitlines():
        line = line.strip()
        token = line.lower()
        if token in SECTIONS:
            next_section = SECTIONS.index(token)
            if next_section <= section:
                raise DeckError("The YDK has duplicate or out-of-order sections.")
            section = next_section
        elif not line or line.startswith("#") or section == -1 and token.startswith("created by"):
            continue
        elif section >= 0 and re.fullmatch(r"[0-9]{1,10}", line) and 0 < int(line) <= 0xFFFFFFFF:
            sections[section].append(line)
        else:
            raise DeckError("Format Library didn’t return a valid YDK decklist.")
    if not sections[0]:
        raise DeckError("The YDK has no main-deck cards.")
    return tuple(tuple(values) for values in sections)


def encode_ydk(sections: Sections) -> bytes:
    lines = [line for marker, cards in zip(SECTIONS, sections, strict=True) for line in (marker, *cards)]
    return ("\n".join(lines) + "\n").encode()


def database_paths(root: Path, unreadable: list[str] | None = None) -> list[Path]:
    """Enumerate playable CDBs in the archived importer's override order."""
    root = root.resolve()
    expansions, repositories = root / "expansions", root / "repositories"
    delta = repositories / "delta-bagooska"

    def error(exc: OSError) -> None:
        if unreadable is not None and not isinstance(exc, FileNotFoundError):
            unreadable.append(str(exc.filename or root))

    paths = []

    def add(path: Path) -> None:
        try:
            if path.resolve().is_relative_to(root) and path.is_file() and path.stat().st_size:
                paths.append(path)
        except OSError as exc:
            error(exc)

    add(root / "cards.cdb")
    try:
        for path in sorted(expansions.glob("*.cdb")):
            add(path)
    except OSError as exc:
        error(exc)
    for folder, dirs, files in os.walk(repositories, onerror=error):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        parent = Path(folder)
        for filename in sorted(files):
            if filename.endswith(".delta.cdb") or (
                parent == delta and filename.startswith(("prerelease-", "release-")) and filename.endswith(".cdb")
            ):
                add(parent / filename)

    def provisional(path: Path) -> bool:
        return (
            path.parent == delta
            and path.name.startswith(("prerelease-", "release-"))
            and not path.name.endswith(".delta.cdb")
        )

    return sorted(paths, key=lambda path: not provisional(path))


@dataclass(frozen=True)
class _Card:
    name: str
    alias: int | None
    scope: int | None
    kind: int | None


def _cards(root: Path) -> tuple[dict[int, _Card], list[str]]:
    cards, unreadable = {}, []
    for path in database_paths(root, unreadable):
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                columns = {column[1] for column in db.execute("PRAGMA table_info(datas)")}
                alias = "datas.alias" if "alias" in columns else "NULL"
                scope = "datas.ot" if "ot" in columns else "NULL"
                kind = "datas.type" if "type" in columns else "NULL"
                sql = f"SELECT datas.id, texts.name, {alias}, {scope}, {kind} FROM datas JOIN texts USING (id)"
                for code, name, family, scope, kind in db.execute(sql):
                    if isinstance(name, str):
                        cards[code] = _Card(
                            name, family if isinstance(family, int) and 0 <= family <= 0xFFFFFFFF else None, scope, kind
                        )
        except sqlite3.Error:
            unreadable.append(path.name)
    return cards, unreadable


def artwork_replacements(section, printings, cards) -> dict[int, int]:
    """Prove missing or non-deckable artwork IDs by matching counts and names in one source section."""
    source = Counter(map(int, section))
    published = Counter(code for code, _ in printings)
    if source.total() != published.total() or 0 in published:
        return {}
    names = defaultdict(set)
    for code, name in printings:
        names[code].add(name.casefold())
    remaining = published - source
    candidates = {}
    for variant, copies in (source - published).items():
        plausible = [base for base, count in remaining.items() if count and 0 < abs(base - variant) < 10]
        if len(plausible) != 1 or remaining[plausible[0]] < copies:
            return {}
        base = plausible[0]
        remaining[base] -= copies
        old, new = cards.get(variant), cards.get(base)
        if (
            variant not in published
            and (old is None or not _deckable(old.kind))
            and new is not None
            and _deckable(new.kind)
            and names[base] == {new.name.casefold()}
        ):
            candidates[variant] = base
    return candidates


def _deckable(kind: int | None) -> bool:
    return kind is None or bool(kind & 7 and not kind & 0x4000)


def _match_artwork(sections: Sections, cards: dict[int, _Card], preview: DeckPreview | None) -> dict[int, int]:
    if not preview or preview.original != sections or len(preview.printings) != len(sections):
        return {}
    confirmed: dict[int, set[int]] = defaultdict(set)
    for section, printings in zip(sections, preview.printings, strict=True):
        replacements = artwork_replacements(section, printings, cards)
        for code in map(int, section):
            confirmed[code].add(replacements.get(code, code))
    return {code: next(iter(bases)) for code, bases in confirmed.items() if code not in bases and len(bases) == 1}


def _identity(name: str) -> str:
    return HISTORICAL.sub("", name).casefold()


def _versions(limits: dict[int, int], cards: dict[int, _Card]) -> dict[tuple[int, str], list[int]]:
    versions = defaultdict(list)
    for code, cap in limits.items():
        card = cards.get(code)
        if card and card.alias and cap > 0 and HISTORICAL.search(card.name):
            versions[card.alias, _identity(card.name)].append(code)
    return versions


def prepare(
    install: Install,
    limits: dict[int, int],
    source: bytes,
    preview: DeckPreview | None = None,
    *,
    stock_tcg: bool = False,
    sizes: dict | None = None,
) -> PreparedDeck:
    """Return the candidate YDK and every substitution or unresolved legality issue; leave all writes to storage."""
    sections = read_ydk(source)
    if preview and preview.original and preview.original != sections:
        raise DeckChanged("Format Library changed this deck. Reload the preview before downloading.")
    cards, unreadable = _cards(install.root)
    if not cards or unreadable:
        issue = "Couldn’t verify card versions because the selected game's card databases are missing or unreadable."
        return PreparedDeck(source, (), (issue,))
    if not limits:
        return PreparedDeck(source, (), ("The historical whitelist is unavailable. Card versions could not be verified.",))

    artwork = _match_artwork(sections, cards, preview)
    versions = {} if stock_tcg else _versions(limits, cards)
    codes = [int(token) for section in sections for token in section]
    resolved_codes = [artwork.get(code, code) for code in codes]
    totals = Counter((cards[code].alias or code) if code in cards else code for code in resolved_codes)
    replacements = {}
    changes, issues = [], []
    if sizes is not None:
        for key, cards_in_section in zip(("main", "extra", "side"), sections, strict=True):
            minimum, maximum = sizes[key]
            if not minimum <= len(cards_in_section) <= maximum:
                issues.append(
                    f"{key.title()} has {len(cards_in_section)} cards. The host recipe allows {minimum}–{maximum}."
                )
    for original, copies in Counter(codes).items():
        code = artwork.get(original, original)
        card = cards.get(code)
        if not card or card.alias is None:
            issues.append(f"{original} · Card identity unavailable in this installation.")
            continue
        family = card.alias or code
        target = code
        cap = limits.get(code)
        if stock_tcg:
            if card.scope not in (2, 3) or card.kind is None or card.kind & (0x4000 | 0x8000000):
                issues.append(f"{card.name} ({original}) · Not a playable TCG printing in this installation.")
                continue
            # Ordinary lists inherit all aliases and allow unlisted cards at three.
            cap = limits.get(code, limits.get(family, 3))
        if cap is None and card.alias and min((code - family) & 0xFFFFFFFF, (family - code) & 0xFFFFFFFF) < 10:
            cap = limits.get(family)
        if cap is None and limits.get(family) == 0:
            cap = 0
        if cap is None:
            choices = versions.get((family, _identity(card.name)), [])
            if len(choices) != 1:
                choices = [candidate for candidate in choices if ARTWORK.get(candidate) == code]
            if len(choices) != 1:
                issues.append(f"{card.name} ({original}) · No unique legal historical printing.")
                continue
            target = choices[0]
            cap = limits[target]
        if cap == 0:
            issues.append(f"{card.name} · Forbidden in this format.")
        elif totals[family] > min(cap, 3):
            issues.append(f"{card.name} · {totals[family]} copies across the deck exceed the limit of {cap}.")
        elif target != original:
            replacements[original] = str(target)
            changes.append(f"{copies} × {cards[target].name} · {original} → {target}")
    if not replacements:
        return PreparedDeck(source, (), tuple(issues))
    revised = tuple(tuple(replacements.get(int(code), code) for code in section) for section in sections)
    return PreparedDeck(encode_ydk(revised), tuple(changes), tuple(issues))

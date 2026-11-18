"""Bounded clipboard decoding and explicit, collision-safe copy publication."""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from pathlib import Path

from tournament_decks import MAX_YDK, SECTIONS, DeckError, publish_ydk

from .cards import Catalog, passcode
from .model import Preview, Sections, preview

# A one-digit YDK passcode takes at least two bytes including its line ending.
MAX_CARDS = MAX_YDK // 2
# Three separately padded base64 sections, plus the scheme and delimiters.
MAX_CODE = 10 + 4 * ((4 * MAX_CARDS + 2) // 3 + 2)


def _check(sections: Sections) -> None:
    if sum(map(len, sections)) > MAX_CARDS:
        raise DeckError(f"Deck exceeds the {MAX_CARDS}-card sharing limit.")
    if any(not passcode(c) for s in sections for c in s):
        raise DeckError("Invalid card passcode.")


def encode_ydke(sections: Sections) -> str:
    """EDOPro 41.0.2 DeckManager::ExportDeckBase64, three packed uint32 sections."""
    _check(sections)
    fields = [base64.b64encode(struct.pack(f"<{len(s)}I", *s)).decode("ascii") for s in sections]
    return "ydke://" + "!".join(fields) + "!"


def preview_ydke(code: str, catalog: Catalog) -> Preview:
    if len(code) > MAX_CODE:
        raise DeckError("Sharing code is too large.")
    code = code.strip()
    fields = code.removeprefix("ydke://").split("!")
    if not code.startswith("ydke://") or len(fields) != 4 or fields[-1]:
        raise DeckError("Expected a ydke:// code with three sections ending in '!'.")
    sections = []
    for field in fields[:3]:
        try:
            raw = base64.b64decode(field, validate=True)
            sections.append(tuple(row[0] for row in struct.iter_unpack("<I", raw)))
        except (ValueError, struct.error) as exc:
            raise DeckError("Malformed base64 or incomplete 32-bit passcode.") from exc
    result = Sections(*sections)
    _check(result)
    return preview(result, catalog)


def save_copy(game: Path, name: str, reviewed: Preview) -> Path:
    """Save the reviewed contents in a new .ydk file, including on repeat saves."""
    _check(reviewed.sections)
    lines = ["#created by EDOPro Studio (new copy)"]
    for marker, section in zip(SECTIONS, reviewed.sections, strict=True):
        lines.append(marker)
        lines.extend(map(str, section))
    content = ("\n".join(lines) + "\n").encode()
    if len(content) > MAX_YDK:
        raise DeckError(f"The new YDK copy would exceed the {MAX_YDK}-byte limit.")
    path, _ = publish_ydk(game, name, content)
    return path


@dataclass(frozen=True)
class Repair:
    before: int
    after: int
    copies: int


@dataclass(frozen=True)
class RepairPreview:
    copy: Preview
    changes: tuple[Repair, ...]
    unresolved: dict[int, str]


def preview_repair(deck: Preview, catalog: Catalog) -> RepairPreview:
    """Propose only unique installed successors for missing passcodes, preserving order."""
    replacements = {}
    unresolved = {}
    for code in deck.copies:
        if code in catalog.cards:
            continue
        if catalog.issues:
            unresolved[code] = "Incomplete installed data prevents verifying a unique successor"
            continue
        targets = catalog.successors.get(code, set())
        if len(targets) == 1:
            target = next(iter(targets))
            if target in catalog.cards and target not in catalog.conflicts:
                replacements[code] = target
                continue
        unresolved[code] = (
            "Several possible successors" if len(targets) > 1 else "No verified installed successor"
        )
    sections = Sections(*(tuple(replacements.get(c, c) for c in s) for s in deck.sections))
    changes = tuple(
        Repair(code, target, sum(deck.copies[code])) for code, target in replacements.items()
    )
    return RepairPreview(preview(sections, catalog), changes, unresolved)

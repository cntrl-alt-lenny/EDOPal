"""Local preflight under standard deck sizes, with explicit gaps in client confidence."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .banlists import Banlist
from .cards import Card, Catalog
from .library import Library
from .model import Counts, Preview, SavedDeck

Status = Literal["legal", "illegal", "could not verify"]
LIST_VIOLATIONS = {"forbidden", "limited", "semi-limited", "outside-whitelist"}


@dataclass(frozen=True)
class Finding:
    rule: str
    reason: str
    ids: tuple[int, ...] = ()
    uncertain: bool = False


@dataclass(frozen=True)
class Verdict:
    status: Status
    findings: tuple[Finding, ...]


def _limit(card: Card, banlist: Banlist) -> int | None:
    # EDOPro 41.0.2 LFList::GetLimitationIterator prefers the exact ID.
    if card.id in banlist.limits:
        return banlist.limits[card.id]
    alias = card.alias
    if alias and (
        not banlist.whitelist
        or min((alias - card.id) & 0xFFFFFFFF, (card.id - alias) & 0xFFFFFFFF) < 10
    ):
        return banlist.limits.get(alias)
    return None


def _placement(card: Card, counts: Counts) -> Finding | None:
    extra_type = card.type & (0x40 | 0x2000 | 0x800000) or (card.type & 0x4000001) == 0x4000001
    if card.type & 0x4000:
        return Finding(
            "token", f"{card.name} is a token and cannot be in a saved playing deck.", (card.id,)
        )
    if counts.extra and card.type & 0x81 == 0x81:
        return Finding(
            "ritual-placement",
            f"{card.name} in Extra requires a room allowing Rituals there.",
            (card.id,),
            True,
        )
    if counts.main and extra_type or counts.extra and not extra_type:
        return Finding(
            "placement",
            f"{card.name} belongs in {'Extra' if extra_type else 'Main'} or Side.",
            (card.id,),
        )
    return None


def preflight(deck: SavedDeck | Preview, banlist: Banlist, catalog: Catalog) -> Verdict:
    """Check local list and standard construction. 'legal' is scoped to those checks."""
    if isinstance(deck, SavedDeck):
        if deck.preview is None:
            return Verdict("could not verify", (Finding("deck-file", deck.error, uncertain=True),))
        deck = deck.preview
    findings = [Finding("list-data", issue, uncertain=True) for issue in banlist.issues]
    if catalog.issues:
        findings.append(
            Finding("catalog-data", "Installed card data was only partially read.", uncertain=True)
        )
    special = bool(re.search(r"\b(rush|speed)\b", banlist.name, re.I))
    if special:
        findings.append(
            Finding(
                "format",
                "Rush/Speed sizes, skills, Legend limits and room rules need client verification.",
                uncertain=True,
            )
        )
    for section, count, minimum, maximum in zip(
        Counts._fields, deck.counts, (40, 0, 0), (60, 15, 15), strict=True
    ):
        if not minimum <= count <= maximum:
            findings.append(
                Finding(
                    "size",
                    f"{section.title()} has {count} cards (standard range {minimum}–{maximum}).",
                    uncertain=special,
                )
            )

    families: defaultdict[int, list[int]] = defaultdict(list)
    names: defaultdict[str, list[int]] = defaultdict(list)
    for code, counts in deck.copies.items():
        card = catalog.cards.get(code)
        if not card:
            findings.append(
                Finding(
                    "unresolved",
                    f"Passcode {code} is missing from installed databases.",
                    (code,),
                    True,
                )
            )
            continue
        if code in catalog.conflicts:
            findings.append(
                Finding(
                    "conflict",
                    f"Installed databases disagree about {card.name} ({code}).",
                    (code,),
                    True,
                )
            )
            continue
        families[card.alias or code].append(code)
        names[card.name.casefold()].append(code)
        base = catalog.cards.get(card.alias) if card.alias else None
        if (
            card.alias is None
            or card.alias
            and (base is None or base.alias != 0 or base.id in catalog.conflicts)
        ):
            findings.append(
                Finding(
                    "alias",
                    f"The alias identity of {card.name} ({code}) is incomplete or chained.",
                    (code,),
                    True,
                )
            )
        if card.scope not in (1, 2, 3) or code >= 100_000_000:
            findings.append(
                Finding(
                    "scope",
                    f"{card.name} ({code}) has custom, historical or unverified card scope.",
                    (code,),
                    True,
                )
            )
        elif card.scope != 3:
            findings.append(
                Finding(
                    "scope",
                    f"The room's allowed pool determines whether region-specific {card.name} ({code}) can be used.",
                    (code,),
                    True,
                )
            )
        if card.kind == "other":
            findings.append(
                Finding(
                    "type", f"Cannot verify the type or skill rules for {card.name}.", (code,), True
                )
            )
        elif placement := _placement(card, counts):
            findings.append(placement)

    for ids in names.values():
        total = sum(sum(deck.copies[code]) for code in ids)
        if len({catalog.cards[c].alias or c for c in ids}) > 1:
            name = catalog.cards[ids[0]].name
            if total > 3:
                findings.append(
                    Finding(
                        "name-count", f"{name} has {total} copies by name (maximum 3).", tuple(ids)
                    )
                )
            findings.append(
                Finding(
                    "alias",
                    f"Installed IDs with the name {name} have different alias identities.",
                    tuple(ids),
                    True,
                )
            )

    # The client increments the alias family's count before checking each printing.
    # A printing with an exact-ID exception can therefore pass or fail on its position.
    totals: Counter[int] = Counter()
    last_count: dict[int, int] = {}
    for section in deck.sections:
        for code in section:
            if code in catalog.cards and code not in catalog.conflicts:
                family = catalog.cards[code].alias or code
                totals[family] += 1
                last_count[code] = totals[family]

    for family, ids in families.items():
        total = totals[family]
        label = catalog.cards[ids[0]].name
        if total > 3:
            findings.append(
                Finding(
                    "copy-count",
                    f"{label} has {total} copies across sections (maximum 3).",
                    tuple(ids),
                )
            )
        caps = {code: _limit(catalog.cards[code], banlist) for code in ids}
        mixed = len(set(caps.values())) > 1
        if mixed:
            findings.append(
                Finding(
                    "alias-limits",
                    f"Printings of {label} have different list limits. Their order affects the client check.",
                    tuple(ids),
                    True,
                )
            )
        for cap in dict.fromkeys(caps.values()):
            prints = tuple(c for c in ids if caps[c] == cap)
            count = max(last_count[c] for c in prints)
            if cap is None:
                if banlist.whitelist:
                    for code in prints:
                        findings.append(
                            Finding(
                                "outside-whitelist",
                                f"{catalog.cards[code].name} ({code}) is absent from the whitelist.",
                                (code,),
                                catalog.cards[code].alias is None or bool(banlist.issues),
                            )
                        )
            elif cap < 3 and count > cap:
                # 41.0.2 tests count > entry. The installed Rush -1 entry rejects every copy.
                rule = ("forbidden", "limited", "semi-limited")[max(cap, 0)]
                reason = f"{label} ({family}) reached copy {count}, above the {banlist.name} limit of {cap}."
                findings.append(Finding(rule, reason, prints, uncertain=bool(banlist.issues)))
    status: Status = "legal"
    if any(not finding.uncertain for finding in findings):
        status = "illegal"
    elif findings:
        status = "could not verify"
    return Verdict(status, tuple(findings))


def banlist_impact(library: Library, banlist: Banlist) -> dict[Path, Verdict]:
    """Return paths and verdicts for definite violations caused by this selected list."""
    affected = {}
    for deck in library.decks:
        verdict = preflight(deck, banlist, library.catalog)
        if any(f.rule in LIST_VIOLATIONS and not f.uncertain for f in verdict.findings):
            affected[deck.path] = verdict
    return affected

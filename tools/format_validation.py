"""Sample all three deck sections after local artwork and historical printing selection."""

from __future__ import annotations

from collections import Counter, defaultdict

from config import BUILTIN_MANUAL_MAP
from tools.format_pool import Database, Index, Pool, effective, normalize
from tournament_decks import DeckPreview, deck_printing_names, match_local_printings, read_ydk


def check_deck(
    event: str, deck: dict, raw: bytes, row: dict, pool: Pool, index: Index, db: Database
) -> dict:
    # Two published Baby Ruler files split the creator comment across three lines.
    # Strip that exact preamble, preserving every section and card slot for the reader.
    def source(text: str) -> tuple[tuple[str, ...], ...]:
        return read_ydk(text.replace("\r\n", "\n").removeprefix("#created\nby\n...\n"))

    original = source(raw.decode("utf-8-sig"))
    sections = ("main", "extra", "side")
    printings = tuple(
        tuple((int(c["artworkId"]), c["name"]) for c in deck[key]) for key in sections
    )
    matched, changes = match_local_printings(
        original,
        deck_printing_names((i, p.name, p.kind) for i, p in db.items()),
        DeckPreview((), printings, source(deck["ydk"])),
    )
    seen: Counter = Counter()
    failures = []
    conversions = []
    for key, codes, originals in zip(sections, matched, original, strict=True):
        printed: dict[int, set[int]] = defaultdict(set)
        for card in deck[key]:
            printed[int(card["artworkId"])].add(card["id"])
        # If a token collision could not be resolved, retain the published identity in the audit.
        downloaded = Counter(map(int, originals))
        published = Counter(int(card["artworkId"]) for card in deck[key])
        missing = published - downloaded
        for value, start in zip(codes, originals, strict=True):
            code = int(value)
            ids = printed.get(code) or index.inverse.get(code, set())
            fid = next(iter(ids)) if len(ids) == 1 else None
            card = index.cards.get(fid, {})
            p = db.get(code)
            cap = effective(pool.entries, code, p.alias) if p else None
            if cap is None and (choices := pool.targets.get(fid)):
                root = index.families[fid][0]
                pinned = [v for v in choices if BUILTIN_MANUAL_MAP.get(str(v)) == value]
                if root in choices:
                    code = root
                elif len(pinned) == 1:
                    code = pinned[0]
                elif len({(db[v].alias, normalize(db[v].name)) for v in choices}) == 1:
                    code = min(choices)
                p = db.get(code)
                cap = effective(pool.entries, code, p.alias) if p else None
            if code != int(start):
                conversions.append([key, int(start), code])
            family = (p.alias or code) if p else code
            seen[family] += 1
            if cap is None or seen[family] > min(cap, 3):
                reason = (
                    "No eligible printing"
                    if cap is None
                    else f"Copy {seen[family]} exceeds limit {cap}"
                )
                when = card.get(row["category"].lower() + "Date")
                if cap is None and when and when > row["date"]:
                    reason = f"Regional release {when} follows the snapshot"
                if p and p.kind & 0x4000:
                    reason = "The downloaded printing is a token"
                    candidates = [
                        v
                        for v in missing
                        if 0 < abs(v - code) < 10
                        and missing[v] == downloaded[code] - published[code]
                    ]
                    if len(candidates) == 1 and len(printed[candidates[0]]) == 1:
                        card = index.cards[next(iter(printed[candidates[0]]))]
                        reason = f"The downloaded printing resolves to {p.name} in EDOPro."
                failures.append(
                    dict(
                        section=key,
                        code=code,
                        name=card.get("name") or (p.name if p else f"Unknown printing {code}"),
                        reason=reason,
                    )
                )
    return dict(
        event=deck.get("eventAbbreviation") or event,
        event_name=event,
        deck=deck["id"],
        deck_type=deck.get("deckTypeName") or "Published deck",
        deck_format=deck["formatName"],
        slots=sum(map(len, original)),
        artwork_changes=changes,
        conversions=conversions,
        failures=failures,
    )


def evidence(samples: list[dict]) -> dict:
    failures = sum(len(s["failures"]) for s in samples)
    count = len(samples)
    if failures:
        label = f"{failures} sampled card slot{'s' if failures != 1 else ''} rejected"
    elif count:
        label = f"Verified against {count} tournament deck{'s' if count != 1 else ''}"
    else:
        label = "Built from release dates · no published decks to check"
    return dict(
        label=label,
        decks=count,
        slots=sum(s["slots"] for s in samples),
        failures=failures,
        samples=samples,
    )

"""Compare installed decks with one verified historical card pool, entirely in memory."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from threading import Event

from decks.banlists import Banlist
from decks.cards import Catalog
from decks.legality import Verdict, _limit, preflight
from decks.model import Preview
from formats.catalogue import Catalogue

from .bots import Bot, Distance, Opponents


def distance(deck: Preview, pool: Banlist, catalog: Catalog) -> Distance:
    # Count copies in client order, across Main, Extra and Side. A copy outside
    # the pool already counts once, even if it is also the fourth of its name.
    seen: Counter[int] = Counter()
    names: Counter[str] = Counter()
    outside = excess = unknown = 0
    for section in deck.sections:
        for code in section:
            card = catalog.cards.get(code)
            if card is None or code in catalog.conflicts or card.alias is None:
                unknown += 1
                continue
            family = card.alias or code
            seen[family] += 1
            name = card.name.casefold()
            names[name] += 1
            if card.alias and (
                family not in catalog.cards
                or catalog.cards[family].alias != 0
                or family in catalog.conflicts
            ):
                unknown += 1
                continue
            cap = _limit(card, pool)
            if cap is None and pool.whitelist:
                outside += 1
            elif seen[family] > (3 if cap is None else min(3, cap)) or names[name] > 3:
                excess += 1
    return Distance(outside, excess, unknown)


@dataclass
class Matchups:
    bots: list[Bot]
    summary: str

    def detail(self, bot: Bot) -> str:
        count = bot.distance
        return count.describe() if count else "Deck unavailable"


def check_format(catalogue: Catalogue, fid: int, opponents: Opponents, cancel: Event) -> Matchups:
    pool = catalogue.bundled_list(fid)
    name = next(row["name"] for row in catalogue.rows if row["id"] == fid)
    ranked = []
    for bot in opponents.bots:
        if cancel.is_set():
            raise InterruptedError("Format check cancelled.")
        count = None
        verdict = Verdict("could not verify", ())
        if bot.preview and opponents.catalog:
            verdict = preflight(bot.preview, pool, opponents.catalog)
            count = distance(bot.preview, pool, opponents.catalog)
        checked = replace(bot, checks=(verdict,), distance=count)
        rank = (
            verdict.status != "legal",
            count is None or bool(count.unknown),
            count.outside + count.excess if count else float("inf"),
            bot.name.casefold(),
        )
        ranked.append((rank, checked))
    bots = [b for _, b in sorted(ranked, key=lambda pair: pair[0])]
    fits = sum(b.fit(0) == "Fits" for b in bots)
    uncertain = sum(b.fit(0) == "Cannot tell" for b in bots)
    if not bots:
        summary = "No practice opponents are installed."
    elif fits:
        summary = f"{fits} opponent{'s fit' if fits != 1 else ' fits'} {name}."
    else:
        summary = (
            f"No confirmed fit for {name}." if uncertain else f"No bundled opponent fits {name}."
        )
        closest = next((b for b in bots if b.distance and not b.distance.unknown), None)
        if closest and closest.distance:
            summary += f" Closest by card limits: {closest.name} · {closest.distance.describe()}."
    if uncertain:
        summary += f" Could not verify {uncertain}."
    return Matchups(bots, summary)

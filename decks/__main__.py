"""Read-only report for the selected installation. All caches live in memory."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from . import DeckIndexer, Library, preflight, version_groups


def report(library: Library) -> None:
    malformed = sum(deck.preview is None for deck in library.decks)
    unresolved = {c for d in library.decks if d.preview for c in d.preview.unresolved}
    print(
        f"{len(library.decks)} decks, {malformed} unreadable, {len(unresolved)} unresolved IDs, "
        f"{len(library.catalog.cards)} installed card IDs, {len(library.banlists)} banlists"
    )
    for issue in library.issues:
        print(f"  Input issue: {issue}")
    for deck in library.decks:
        detail = "/".join(map(str, deck.preview.counts)) if deck.preview else deck.error
        print(f"  {deck.path.relative_to(library.game / 'deck')}  {detail}")
    groups = version_groups(library.decks)
    print(f"Version groups: {len(groups)}")
    for group in groups:
        print("  " + " | ".join(str(d.path.relative_to(library.game / "deck")) for d in group))
    print("Preflight uses standard local construction rules and the selected installed list.")
    totals: Counter[str] = Counter()
    for banlist in library.banlists:
        print(f"{banlist.name} [{banlist.path.relative_to(library.game)}:{banlist.line}]")
        for deck in library.decks:
            verdict = preflight(deck, banlist, library.catalog)
            totals[verdict.status] += 1
            print(f"  {deck.path.relative_to(library.game / 'deck')} — {verdict.status}")
            for finding in verdict.findings:
                label = "could not verify" if finding.uncertain else finding.rule
                print(f"    {label}: {finding.reason}")
    print(
        f"Preflights: {sum(totals.values())} ({totals['legal']} legal, "
        f"{totals['illegal']} illegal, {totals['could not verify']} could not verify)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, metavar="INSTALLATION")
    args = parser.parse_args()
    try:
        result = DeckIndexer(args.report).scan()
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    report(result)


if __name__ == "__main__":
    main()

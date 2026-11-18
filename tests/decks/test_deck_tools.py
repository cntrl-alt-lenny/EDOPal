from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from threading import Event

import pytest

from decks import DeckIndexer, Sections, compare
from decks.duplicates import difference, find_duplicates
from decks.history import DAY_NS, RETENTION_DAYS, VERSIONS_PER_DECK, DeckStore
from decks.odds import opening_combo, opening_distribution

from .conftest import write_deck


def test_history_edits_moves_missing_and_tag_membership(game: Path, tmp_path: Path) -> None:
    index = DeckIndexer(game)
    path = game / "deck/Family/Trial v1.YDK"
    store = DeckStore(tmp_path / "profile/deck-tools.sqlite3")
    old = index.scan()
    track = store.observe(old, 10).active[path]
    original = store.timeline(track).latest
    assert not store.timeline(track).previous
    (group,) = store.add_group(track, "  My   starters ")
    assert group.name == "My starters"
    store.set_tags(track, {(group.id, 100): True})
    current = Sections((100, 190, 190, 1), (210,), (101, 200))
    write_deck(path, current)
    new = index.scan()
    assert store.observe(new, 20).active[path] == track
    assert store.timeline(track).previous == (original,)
    assert store.timeline(track).latest.sections == current
    store.observe(old, 15)
    assert store.timeline(track).latest.sections == current

    renamed = game / "deck/Renamed.ydk"
    path.rename(renamed)
    moved = store.observe(index.scan(), 30).active[renamed]
    assert moved.id == track.id and store.groups(moved)[0].cards == {100}
    assert store.timeline(moved).previous == (original,)
    renamed.unlink()
    missing = store.observe(index.scan(), 40).missing
    assert moved in missing and store.timeline(moved).missing
    assert store.timeline(moved).latest.sections == current
    assert not (game / "deck/deck-tools.sqlite3").exists()
    with pytest.raises(ValueError, match="outside"):
        DeckStore(game / "deck/deck-tools.sqlite3").observe(index.scan(), 50)


def test_history_rejects_ambiguous_move_and_prunes(game: Path, tmp_path: Path) -> None:
    index = DeckIndexer(game)
    store = DeckStore(tmp_path / "tools.sqlite3")
    path = game / "deck/history.ydk"
    for revision in range(VERSIONS_PER_DECK + 5):
        write_deck(path, Sections((revision + 1,)))
        track = store.observe(index.scan(), 100 + revision).active[path]
    versions = store.timeline(track).previous
    assert len(versions) == VERSIONS_PER_DECK
    assert versions[0].sections.main == (VERSIONS_PER_DECK + 4,)
    renamed = game / "deck/reused.ydk"
    # A coincident file ID with changed contents cannot claim the old path's history.
    path.rename(renamed)
    write_deck(renamed, Sections((100,)))
    receipt = store.observe(index.scan(), 200)
    assert receipt.active[renamed].id != track.id and track in receipt.missing
    clean = store.observe(index.scan(), 201 + RETENTION_DAYS * DAY_NS)
    assert track not in clean.missing
    assert not store.timeline(clean.active[renamed]).previous


def test_state_concurrent_tag_edits_and_cancellation(game: Path, tmp_path: Path) -> None:
    index = DeckIndexer(game)
    snapshot = index.scan()
    path = tmp_path / "tools.sqlite3"
    store = DeckStore(path)
    track = next(iter(store.observe(snapshot, 10).active.values()))
    (group,) = store.add_group(track, "Starters")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(DeckStore(path).set_tags, track, {(group.id, code): True})
            for code in range(1, 15)
        ]
        for future in futures:
            future.result()
    assert store.groups(track)[0].cards == set(range(1, 15))
    with pytest.raises(ValueError, match="already exists"):
        store.add_group(track, "STARTERS")
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        index.scan(cancel=cancel)
    with pytest.raises(InterruptedError):
        store.observe(snapshot, 20, cancel)
    assert store.remove_group(track, group.id) == ()


@pytest.mark.parametrize("hand", [5, 6])
def test_odds_match_exhaustive_hands_with_overlapping_groups(hand: int) -> None:
    main = (1, 1, 2, 2, 3, 4, 5, 6, 7)
    hands = list(combinations(main, hand))
    starters, bricks = {1, 2, 3}, {2, 4}
    counts = Counter(sum(code in starters for code in draw) for draw in hands)
    probabilities = opening_distribution(Counter(main), starters, hand)
    assert probabilities == tuple(Fraction(counts[n], len(hands)) for n in range(hand + 1))
    combo = sum(bool(set(draw) & starters) and not set(draw) & bricks for draw in hands)
    assert opening_combo(Counter(main), starters, bricks, hand) == Fraction(combo, len(hands))
    assert opening_distribution(Counter(main), (), hand)[0] == 1
    assert opening_distribution({1: 1}, starters, hand) == ()
    assert opening_combo({1: 1}, starters, bricks, hand) is None


def test_duplicate_groups_ignore_order_and_report_direction(game: Path) -> None:
    folder = game / "deck/tools"
    first = Sections(tuple(range(1, 41)), (210,), (42, 43))
    exact = Sections(tuple(reversed(first.main)), first.extra, (43, 42))
    moved = Sections(first.main[:-1], (210,), (40, 42, 43))
    replacement = Sections(first.main[:-4] + (180, 190, 200), (210,), (40, 42, 43))
    names = ("Alpha", "Different name", "Moved", "Bridge", "Unrelated")
    paths = tuple(
        write_deck(folder / f"{name}.ydk", section)
        for name, section in zip(
            names, (first, exact, moved, replacement, Sections(tuple(range(400, 410)))), strict=True
        )
    )
    index = find_duplicates(DeckIndexer(game).scan().decks)
    group = index.groups[index.by_path[paths[0]]]
    assert {deck.path for deck in group} == set(paths[:4])
    assert index.marks[paths[1]] == "Identical & similar"
    assert index.marks[paths[2]] == "Similar"
    assert paths[4] not in index.by_path
    assert difference(first, exact) == 0 and difference(first, moved) == 1
    assert difference(first, replacement) == 4
    assert [(c.kind, c.source, c.destination) for c in compare(first, moved)] == [
        ("moved", "main", "side")
    ]

"""Exact counts of unordered opening hands from a saved Main Deck."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from fractions import Fraction
from math import comb


def _choose(population: int, take: int) -> int:
    return comb(population, take) if 0 <= take <= population else 0


def opening_distribution(
    main: Mapping[int, int], group: Collection[int], hand: int
) -> tuple[Fraction, ...]:
    total = sum(main.values())
    denominator = _choose(total, hand)
    if not denominator:
        return ()
    hits = sum(main.get(code, 0) for code in set(group))
    return tuple(
        Fraction(_choose(hits, n) * _choose(total - hits, hand - n), denominator)
        for n in range(hand + 1)
    )


def opening_combo(
    main: Mapping[int, int], want: Collection[int], avoid: Collection[int], hand: int
) -> Fraction | None:
    total = sum(main.values())
    denominator = _choose(total, hand)
    if not denominator:
        return None
    blocked = set(avoid)
    no_avoid = total - sum(main.get(code, 0) for code in blocked)
    neither = no_avoid - sum(main.get(code, 0) for code in set(want) - blocked)
    return Fraction(_choose(no_avoid, hand) - _choose(neither, hand), denominator)

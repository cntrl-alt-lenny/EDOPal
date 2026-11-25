from itertools import permutations

import pytest

from edopal.plugins.decktracker.model import DeckState, LostHistory


def test_report_example_and_empty_deck():
    deck = DeckState.start([1] * 3 + [2] * 32)
    assert deck.probability(1) == pytest.approx(3 / 35)
    assert round(deck.probability(1, 3) * 100, 1) == 24.2
    assert deck.probability(1, 0) == 0
    assert deck.probability(1, 100) == 1
    assert deck.probability(7, 100) == 0
    for code in list(deck.starting.elements()):
        deck.remove(code, drawn=True)
    assert deck.probability(1) is None
    assert deck.starting.total() == 35


def test_conditioning_matches_enumerated_orders():
    cards = [1, 1, 2, 3, 4]
    deck = DeckState.start(cards)
    deck.reveal(1, 2)
    deck.reveal(4, 1)
    cases = [order for order in permutations(cards) if order[1] == 2 and order[-1] == 1]
    for n in range(1, 7):
        for code in set(cards):
            expected = sum(code in order[:n] for order in cases) / len(cases)
            assert deck.probability(code, n) == pytest.approx(expected)


def test_private_order_public_returns_and_unknown_departure():
    deck = DeckState.start([1, 2, 3])
    deck.forget_order()
    assert deck.probability(1) is None
    deck.insert(4, deck.total)
    assert deck.probability(4) == 1
    deck.remove(4, drawn=True)
    assert deck.probability(2) is None
    deck.insert(2, 0)
    deck.forget_order(shuffled=True)
    assert deck.probability(2) == 0.5
    with pytest.raises(LostHistory):
        deck.remove(0)

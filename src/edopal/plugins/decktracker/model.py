"""Player-visible Main Deck state. Positions are offsets from the next draw."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from math import comb


class LostHistory(ValueError):
    """The visible events no longer establish an exact Main Deck."""


@dataclass
class DeckState:
    starting: Counter[int]
    remaining: Counter[int]
    random_order: bool
    known: dict[int, int] = field(default_factory=dict)
    private_prefix: int = 0

    @classmethod
    def start(cls, cards: Iterable[int], *, shuffled: bool = True) -> DeckState:
        counts = Counter(cards)
        if not counts or any(code <= 0 for code in counts):
            raise LostHistory("Missing submitted Main Deck.")
        return cls(counts, counts.copy(), shuffled)

    @property
    def total(self) -> int:
        return self.remaining.total()

    def probability(self, code: int, draws: int = 1) -> float | None:
        """Chance of at least one copy in consecutive draws without replacement."""
        if draws < 0:
            raise ValueError("Draw count must be nonnegative.")
        if draws == 0:
            return 0.0
        total = self.total
        if not total:
            return None
        n = min(draws, total)
        fixed = [card for offset, card in self.known.items() if offset < n]
        if code in fixed:
            return 1.0
        pool = total - len(self.known)
        copies = self.remaining[code] - sum(card == code for card in self.known.values())
        sample = n - len(fixed)
        if not copies or not sample:
            return 0.0
        if sample > pool - copies:
            return 1.0
        if not self.random_order:
            return None
        return 1 - comb(pool - copies, sample) / comb(pool, sample)

    def forget_order(self, *, shuffled: bool = False) -> None:
        self.known.clear()
        self.random_order = shuffled
        self.private_prefix = 0

    def private_top_move(self) -> None:
        # SortDeck emits one zero-code move per card in the privately sorted prefix.
        # Its length is public. Neither the source index nor the permutation is needed.
        prefix = (
            min(self.total, self.private_prefix + 1) if (self.random_order or self.private_prefix) and not self.known else 0
        )
        self.forget_order()
        self.private_prefix = prefix

    def reveal(self, offset: int, code: int) -> None:
        if not 0 <= offset < self.total:
            raise LostHistory("Reveal outside the Main Deck.")
        if not code:
            self.known.pop(offset, None)
            return
        if self.known.get(offset, code) != code:
            raise LostHistory("Top-card reveal disagrees with previous play.")
        self.known[offset] = code
        if sum(card == code for card in self.known.values()) > self.remaining[code]:
            raise LostHistory("Revealed card disagrees with remaining copies.")

    def remove(self, code: int, *, drawn: bool = False) -> None:
        if not code or not self.remaining[code]:
            raise LostHistory("A card leaving the Main Deck could not be identified.")
        if drawn:
            if self.known.get(0, code) != code:
                raise LostHistory("Draw disagrees with the visible top card.")
            self.known = {offset - 1: card for offset, card in self.known.items() if offset}
            if self.private_prefix:
                self.private_prefix -= 1
                self.random_order = self.private_prefix == 0
        elif self.known or self.private_prefix:
            # A search animation does not establish the selected card's original index.
            self.forget_order()
        self.remaining[code] -= 1
        if sum(card == code for card in self.known.values()) > self.remaining[code]:
            raise LostHistory("Draw disagrees with the visible deck.")
        if not self.remaining[code]:
            del self.remaining[code]

    def insert(self, code: int, sequence: int) -> None:
        if not code:
            raise LostHistory("A card entering the Main Deck could not be identified.")
        total = self.total
        if not 0 <= sequence <= total:
            raise LostHistory("Return outside the Main Deck.")
        self.remaining[code] += 1
        if sequence == total:
            if self.private_prefix:
                self.private_prefix += 1
            self.known = {offset + 1: card for offset, card in self.known.items()}
            self.reveal(0, code)
        elif sequence == 0:
            self.reveal(total, code)
        else:
            self.forget_order()

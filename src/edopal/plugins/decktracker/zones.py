"""Track the public locations of cards observed leaving the player's Main Deck.

Untracked occupants are zero. Only aggregate counts enter a published snapshot.
"""

from collections import Counter

HAND, MONSTER, SPELL, GRAVE, BANISHED, EXTRA, MATERIAL = 2, 4, 8, 16, 32, 64, 128
NAMES = {HAND: "Hand", MONSTER: "Field", SPELL: "Field", GRAVE: "GY", BANISHED: "Banished"}


class ZoneLedger:
    def __init__(self, seat, extras):
        self.seat = seat
        self.piles = {(p, z): [] for p in (0, 1) for z in (HAND, GRAVE, BANISHED)}
        self.field = {}
        self.attached = {}
        self.extra = [[(0, False)] * n for n in extras]
        self.shuffled = []  # (indistinguishable field slots, multiset of their tracked identities)
        self.mixed_deck = set()

    def put(self, loc, code):
        player, zone, sequence, pos = loc
        if (player, zone) in self.piles:
            self.piles[player, zone].append(0 if zone == HAND and player != self.seat else code)
        elif zone in (MONSTER, SPELL):
            self.field[player, zone, sequence] = code
        elif zone & MATERIAL:
            self.attached.setdefault((player, zone & ~MATERIAL, sequence), []).append(code)
        elif zone == EXTRA:
            pile = self.extra[player]
            faceup = bool(pos & 5)
            index = len(pile) if faceup else next((i for i, (_, up) in enumerate(pile) if up), len(pile))
            pile.insert(index, (code, faceup))

    def take(self, loc, visible_code=0):
        player, zone, sequence, pos = loc
        if zone == EXTRA:
            pile = self.extra[player]
            return pile.pop(sequence)[0] if sequence < len(pile) else 0
        if zone & MATERIAL:
            pile = self.attached.get((player, zone & ~MATERIAL, sequence), [])
            return pile.pop(pos) if pos < len(pile) else 0
        pile = self.piles.get((player, zone))
        if pile is not None and sequence < len(pile):
            return pile.pop(sequence)
        key = player, zone, sequence
        for group in self.shuffled:
            slots, cards = group
            if key in slots:
                slots.remove(key)
                if len(cards) == 1:
                    code = next(iter(cards))
                elif visible_code in cards and 0 not in cards:
                    code = visible_code
                else:
                    # The departing copy's provenance cannot be established.
                    # Keep only the independently certain locations.
                    self.shuffled.remove(group)
                    return 0
                cards[code] -= 1
                if not cards[code]:
                    del cards[code]
                if not slots:
                    self.shuffled.remove(group)
                return code
        return self.field.pop(key, 0)

    def move_materials(self, before, after):
        if before[1] == MONSTER and (cards := self.attached.pop(before[:3], None)) and after[1] == MONSTER:
            self.attached[after[:3]] = cards

    def swap(self, left, right, codes):
        a, b = self.take(left, codes[0]), self.take(right, codes[1])
        self.put(right, a)
        self.put(left, b)
        ma, mb = self.attached.pop(left[:3], []), self.attached.pop(right[:3], [])
        if ma:
            self.attached[right[:3]] = ma
        if mb:
            self.attached[left[:3]] = mb

    def shuffle_field(self, locations):
        cards = Counter(self.take(loc) for loc in locations)
        if any(cards):
            self.shuffled.append(({loc[:3] for loc in locations}, cards))
        for loc in locations:
            self.attached.pop(loc[:3], None)

    def shuffle_hand(self, player, visible_cards):
        pile = self.piles[player, HAND]
        known = Counter(pile)
        counts = Counter(visible_cards)
        # Same-name foreign and local copies can become indistinguishable.
        pile[:] = [c if c and known[c] == counts[c] else 0 for c in visible_cards]

    def departure(self, deck, code):
        # After an untracked card with the same ID enters the Main Deck, only
        # its name and draw probability are certain. Never claim its ownership.
        tracked = code if code in deck.starting and code not in self.mixed_deck else 0
        if not deck.remaining[code]:
            self.mixed_deck.discard(code)
        return tracked

    def totals(self):
        result = {name: Counter() for name in NAMES.values()}
        for (_, zone), cards in self.piles.items():
            result[NAMES[zone]].update(code for code in cards if code)
        result["Field"].update(code for code in self.field.values() if code)
        for _, cards in self.shuffled:
            result["Field"].update({code: n for code, n in cards.items() if code})
        for name, cards in (
            ("Material", (c for pile in self.attached.values() for c in pile if c)),
            ("Extra", (c for pile in self.extra for c, _ in pile if c)),
        ):
            if counts := Counter(cards):
                result[name] = counts
        return result

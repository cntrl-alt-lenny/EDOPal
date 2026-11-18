import unittest

from tournament_decks import DeckPreview, match_local_printings


class DeckPrintingTests(unittest.TestCase):
    def setUp(self):
        self.cards = (("76922030", "70828913", "70828913", "55144522"), (), ())
        self.local = {
            76922029: "Don Zaloog",
            70828912: "Premature Burial",
            55144522: "Pot of Greed",
        }
        # The public card details may be sorted differently from the text download.
        self.printings = (
            (
                (70828912, "Premature Burial"),
                (55144522, "Pot of Greed"),
                (70828912, "Premature Burial"),
                (76922029, "Don Zaloog"),
            ),
            (),
            (),
        )

    def test_matches_supported_printings_and_preserves_order_and_quantity(self):
        detail = DeckPreview((), self.printings, self.cards)
        self.assertEqual(
            match_local_printings(self.cards, self.local, detail),
            ((("76922029", "70828912", "70828912", "55144522"), (), ()), 3),
        )

    def test_supported_alternate_printing_stays_exactly_as_downloaded(self):
        self.local[76922030] = "Don Zaloog"
        fixed, count = match_local_printings(
            self.cards, self.local, DeckPreview((), self.printings, self.cards)
        )
        self.assertEqual((fixed[0][0], count), ("76922030", 2))

    def test_disagreed_source_snapshots_or_quantities_cannot_replace_a_card(self):
        for detail in (
            DeckPreview((), self.printings, ()),
            DeckPreview((), (self.printings[0][1:], (), ()), self.cards),
        ):
            self.assertEqual(match_local_printings(self.cards, self.local, detail), (self.cards, 0))

    def test_ambiguous_or_misnamed_neighbors_are_preserved(self):
        cards = (("102", "106"), (), ())
        detail = DeckPreview((), (((100, "Alpha"), (104, "Beta")), (), ()), cards)
        self.assertEqual(
            match_local_printings(cards, {100: "Alpha", 104: "Beta"}, detail), (cards, 0)
        )
        self.local[76922029] = "Another card"
        fixed, _ = match_local_printings(
            self.cards, self.local, DeckPreview((), self.printings, self.cards)
        )
        self.assertEqual(fixed[0][0], "76922030")

import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import tournament_decks as td

EVENT = td.Event("PBR59", "Patron Battle Royale 59", "Edison", "2026-10-07", 58)
DECK = td.Deck(87757, "Gladiator Beast", "morph", 1)
YDK = "created by...\r\n#main\r\n00045827\r\n00045827\r\n#extra\r\n17\r\n!side\r\n99\r\n"


class PublicBrowseTests(unittest.TestCase):
    def setUp(self):
        self.api = td.FormatLibrary()

    @patch("tournament_decks._json")
    def test_format_search_uses_public_sorted_api_and_reuses_page(self, fetch):
        fetch.return_value = [
            {
                "abbreviation": "PBR59",
                "name": EVENT.name,
                "formatName": "Edison",
                "size": 58,
                "startedAt": "2026-10-07T16:34:21.198Z",
            },
            {"abbreviation": "X", "name": "Private", "display": False},
            None,
        ]
        for _ in range(2):
            self.assertEqual(self.api.events("Edison", "PBR", 1), (EVENT,))
        fetch.assert_called_once()
        url = urlsplit(fetch.call_args.args[0])
        self.assertEqual(url.path, "events")
        self.assertEqual(
            parse_qs(url.query),
            {
                "page": ["1"],
                "limit": [str(td.PAGE_SIZE)],
                "sort": ["startedAt:desc"],
                "filter": ["format:eq:Edison,name:or:PBR"],
            },
        )
        fetch.reset_mock()
        with self.assertRaises(td.DeckError):
            self.api.events("Goat", "foo,format:eq:Edison", 1)
        fetch.assert_not_called()

    @patch("tournament_decks._json")
    def test_standings_only_expose_public_identified_decks(self, fetch):
        fetch.return_value = {
            "topDecks": [
                {"id": 2, "deckTypeName": "Frog", "builderName": "second", "placement": "2"},
                {
                    "id": 87757,
                    "deckTypeName": "Gladiator Beast",
                    "builderName": "morph",
                    "placement": 1,
                },
                {"id": 2, "placement": 3},
                {"id": 3, "display": False},
                {"id": "x"},
                {},
            ]
        }
        result = self.api.standings(EVENT)
        self.assertEqual(result, (DECK, td.Deck(2, "Frog", "second", 2)))
        fetch.assert_called_once_with("events/PBR59")

    @patch("tournament_decks._json")
    def test_preview_aggregates_repeated_cards_in_source_order(self, fetch):
        fetch.return_value = {
            "id": 87757,
            "display": True,
            "main": [{"name": "Z"}, {"name": "A"}, {"name": "Z"}],
            "extra": [],
            "side": [{"name": "B"}],
        }
        self.assertEqual(self.api.preview(DECK).sections, ((("Z", 2), ("A", 1)), (), (("B", 1),)))
        fetch.assert_called_once_with("decks/87757")

    @patch("tournament_decks._json")
    def test_restricted_or_mismatched_preview_is_unavailable(self, fetch):
        for payload in ({"display": False, "id": 87757}, {"display": True, "id": 8}, []):
            fetch.return_value = payload
            with self.assertRaises(td.DeckError):
                self.api.preview(DECK)

    @patch("tournament_decks.urlopen")
    def test_rate_limit_has_clear_message_and_no_automatic_retries(self, opening):
        opening.side_effect = HTTPError(td.SITE, 429, "rate limited", {}, None)
        with self.assertRaisesRegex(td.DeckError, "too many requests"):
            self.api.standings(EVENT)
        opening.assert_called_once()

    @patch("tournament_decks._read")
    def test_download_keeps_duplicates_leading_zero_passcodes_and_sections(self, fetch):
        fetch.return_value = YDK
        self.assertEqual(self.api.download(DECK), (("00045827", "00045827"), ("17",), ("99",)))
        fetch.assert_called_once_with("decks/download/87757", limit=td.MAX_YDK)


class YdkTests(unittest.TestCase):
    def test_validates_payload_before_writing(self):
        for bad in (
            "<html>login</html>",
            "#extra\n123\n",
            "#main\n0",
            "#main\n4294967296",
            "#main\n1e3",
            "#main\n123\n#main\n999",
            "123\n#main\n456",
            "#main\n123\n!side\n8\n#extra\n5",
        ):
            with self.subTest(bad=bad), self.assertRaises(td.DeckError):
                td.read_ydk(bad)
        self.assertEqual(td.read_ydk("\ufeff#created by FL\n#main\n1"), (("1",), (), ()))

    def test_retains_large_historical_fusion_deck(self):
        source = "#main\n1\n#extra\n" + "25\n" * 46 + "!side\n"
        self.assertEqual(len(td.read_ydk(source)[1]), 46)


class SaveDeckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.game = Path(tmp.name)
        self.cards = td.read_ydk(YDK)

    def save(self):
        return td.save_deck(self.game, EVENT, DECK, self.cards)

    def test_idempotence_and_personal_edits(self):
        first, added = self.save()
        self.assertTrue(added)
        self.assertEqual(first.parent, self.game / "deck")
        text = first.read_text(encoding="utf-8")
        self.assertIn(DECK.url, text)
        self.assertEqual(td.read_ydk(text), self.cards)
        self.assertEqual(self.save(), (first, False))
        first.write_text("personal edits", encoding="utf-8")
        second, added = self.save()
        self.assertTrue(added)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding="utf-8"), "personal edits")
        self.assertEqual(self.save(), (second, False))
        self.assertEqual(set(first.parent.iterdir()), {first, second})

    def test_remote_labels_cannot_choose_an_output_path(self):
        event = td.Event("../../c:/", "Name", "Goat", "", 32)
        deck = td.Deck(23, "a/b", 'u<>:"/\\|?*\nname', 1)
        dest, _ = td.save_deck(self.game, event, deck, self.cards)
        self.assertEqual(dest.parent, self.game / "deck")
        self.assertNotRegex(dest.name, r'[<>:"/\\|?*\n]')
        self.assertEqual(td.read_ydk(dest.read_text(encoding="utf-8")), self.cards)

    def test_concurrent_repeat_import_publishes_one_complete_file(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _i: self.save(), range(8)))
        self.assertEqual(sum(added for _, added in results), 1)
        self.assertEqual(len({p for p, _ in results}), 1)
        self.assertEqual(td.read_ydk(results[0][0].read_text(encoding="utf-8")), self.cards)
        self.assertEqual(len(list((self.game / "deck").iterdir())), 1)

    def test_missing_game_folder_is_not_created(self):
        with self.assertRaises(td.DeckError):
            td.save_deck(self.game / "missing", EVENT, DECK, self.cards)
        self.assertFalse((self.game / "missing").exists())

    def test_redirected_deck_directory_stays_inside_selected_game(self):
        with tempfile.TemporaryDirectory() as other:
            link = self.game / "deck"
            if os.name == "nt":
                # A directory junction needs no symlink privilege on Windows.
                subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), other],
                    check=True,
                    capture_output=True,
                )
            else:
                link.symlink_to(other, target_is_directory=True)
            try:
                with self.assertRaisesRegex(td.DeckError, "outside"):
                    self.save()
                self.assertEqual(list(Path(other).iterdir()), [])
            finally:
                if os.name == "nt":
                    link.rmdir()
                else:
                    link.unlink()

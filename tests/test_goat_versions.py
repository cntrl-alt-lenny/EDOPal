import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from goat_versions import GoatVersions, installed_aliases, installed_whitelist
from tournament_decks import Deck, Event, save_deck


class WhitelistTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.game = Path(tmp.name)
        self.folder = self.game / "lflists"
        self.folder.mkdir()

    def test_exact_named_whitelist_and_section_boundaries(self):
        self.assertEqual(installed_whitelist(self.game), {})
        (self.folder / "example.conf").write_text(
            "!GOAT\n$whitelist\n88 1\n!2005.4 GOAT\n99 3\n!Other\n$whitelist\n66 1\n",
            encoding="utf-8-sig",
        )
        self.assertEqual(installed_whitelist(self.game), {})
        (self.game / "lflist.conf").write_text(
            "!2005.4 GOAT\n$whitelist\n10 2 --Card\n20 0 --Forbidden\n!OCG\n30 1\n",
            encoding="utf-8-sig",
        )
        self.assertEqual(installed_whitelist(self.game), {10: 2, 20: 0})
        (self.folder / "collision.conf").write_text("!2005.4 GOAT\n$whitelist\n10 3\n")
        self.assertEqual(installed_whitelist(self.game), {})

    def test_repository_must_be_configured_and_enabled(self):
        repo_path = self.game / "repositories" / "custom"
        repo_path.mkdir(parents=True)
        (repo_path / "GOAT.lflist.conf").write_text("!2005.4 GOAT\n$whitelist\n5405694 3\n")
        config = self.game / "config" / "configs.json"
        config.parent.mkdir()
        for enabled in (False, True):
            repo = {
                "repo_path": "./repositories/custom",
                "lflist_path": ".",
                "should_read": enabled,
            }
            config.write_text(json.dumps({"repos": [repo]}), encoding="utf-8")
            self.assertEqual(installed_whitelist(self.game), {5405694: 3} if enabled else {})

    def test_incomplete_alias_schema_or_changed_identity_is_unavailable(self):
        expansions = self.game / "expansions"
        expansions.mkdir()
        with closing(sqlite3.connect(self.game / "cards.cdb")) as db:
            db.executescript("""CREATE TABLE datas(id PRIMARY KEY,alias);CREATE TABLE texts(id PRIMARY KEY,name);
                INSERT INTO datas VALUES(1,2),(3,4),(5,6);
                INSERT INTO texts VALUES(1,'Old'),(3,'Current'),(5,'Renamed');""")
        with closing(sqlite3.connect(expansions / "delta.cdb")) as db:
            db.executescript("""CREATE TABLE datas(id PRIMARY KEY);CREATE TABLE texts(id PRIMARY KEY,name);
                INSERT INTO datas VALUES(1);INSERT INTO texts VALUES(1,'Override');""")
        self.assertEqual(
            installed_aliases(self.game, {1: "Override", 3: "Current", 5: "Old name"}),
            {1: None, 3: 4, 5: None},
        )


class GoatVersionTests(unittest.TestCase):
    def test_official_code_first_then_artwork_alias_with_strict_boundary(self):
        library = GoatVersions(
            {500: 3, 508: 0, 700: 1},
            {500: "Card", 508: "Card", 509: "Card", 510: "Card", 700: "Card (GOAT)"},
            {500: 0, 508: 500, 509: 500, 510: 500, 700: 500},
        )
        self.assertEqual(library.limit(508), 0)
        self.assertEqual(library.limit(509), 3)
        self.assertIsNone(library.limit(510))
        self.assertEqual(library.convert((("00509",), (), ())).sections, (("00509",), (), ()))
        blocked = library.convert((("508",), (), ()))
        self.assertEqual(blocked.sections, (("508",), (), ()))
        self.assertEqual(blocked.review, {508: "forbidden"})
        self.assertEqual(library.convert((("510",), (), ())).sections, (("700",), (), ()))

    def test_anchor_by_both_alias_and_name_and_preserve_section_order(self):
        library = GoatVersions(
            {80: 3, 90: 3, 100: 3, 110: 3},
            {
                10: "A",
                20: "B",
                30: "C",
                80: "A (GOAT)",
                90: "B (GOAT)",
                100: "C (Pre-Errata)",
                110: "C",
            },
            {10: 0, 20: 0, 30: 0, 80: 10, 90: 10, 100: 0, 110: 30},
        )
        original = (("10", "20"), ("30",), ("10",))
        result = library.convert(original)
        self.assertEqual(result.sections, (("80", "20"), ("30",), ("80",)))
        self.assertEqual(result.review, {20: "no verified version", 30: "no verified version"})
        self.assertEqual(result.changed, 2)
        self.assertEqual(original, (("10", "20"), ("30",), ("10",)))

    def test_ring_preserves_known_art_and_unknown_ambiguity_is_reported(self):
        ids = {
            83555666: "Ring of Destruction",
            83555667: "Ring of Destruction",
            83555668: "Ring of Destruction",
            511000824: "Ring of Destruction (Pre-Errata)",
            511000825: "Ring of Destruction (Pre-Errata)",
        }
        aliases = {cid: 83555666 for cid in ids}
        aliases[83555666] = 0
        library = GoatVersions({511000824: 1, 511000825: 1}, ids, aliases)
        for source, expected in ((83555667, "511000824"), (83555666, "511000825")):
            result = library.convert(((str(source),), (), ()))
            self.assertEqual(result.sections, ((expected,), (), ()))
            self.assertEqual(result.review, {})
        result = library.convert((("83555668",), (), ()))
        self.assertEqual(result.sections, (("83555668",), (), ()))
        self.assertEqual(result.review, {83555668: "ambiguous versions"})

    def test_enforce_copy_limit_across_main_extra_and_side_before_converting(self):
        lib = GoatVersions(
            {800: 1}, {100: "Demo", 101: "Demo", 800: "Demo (GOAT)"}, {100: 0, 101: 100, 800: 100}
        )
        original = (("100",), (), ("101",))
        result = lib.convert(original)
        self.assertEqual(result.sections, original)
        self.assertEqual(result.review, {100: "over copy limit", 101: "over copy limit"})

    def test_canonical_forbidden_or_unverified_cards_are_retained(self):
        lib = GoatVersions(
            {100: 0, 900: 1},
            {100: "Old card", 510: "Old card", 900: "Old card (GOAT)"},
            {100: 0, 510: 100, 900: 100},
        )
        original = (("510", "12345678"), (), ())
        result = lib.convert(original)
        self.assertEqual(result.sections, original)
        self.assertEqual(result.review, {510: "forbidden", 12345678: "identity unavailable"})

    def test_distinct_export_names_preserve_original_and_repeat_safely(self):
        event = Event("E", "Event", "Goat", "", 12)
        deck = Deck(9, "Demo", "Player", 1)
        with tempfile.TemporaryDirectory() as game:
            regular, added = save_deck(game, event, deck, (("100",), (), ()))
            self.assertTrue(added)
            goat, added = save_deck(game, event, deck, (("500",), (), ()), goat_versions=True)
            self.assertTrue(added)
            self.assertNotEqual(regular, goat)
            self.assertIn("GOAT versions", goat.name)
            self.assertTrue(regular.read_bytes().endswith(b"#main\n100\n#extra\n!side\n"))
            self.assertEqual(
                save_deck(game, event, deck, (("500",), (), ()), goat_versions=True), (goat, False)
            )

"""End-to-end run() flow tests for the failure-cache and deck-filter invariants."""

import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import main
from config import Config


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, *args, **kwargs):
        return FakeResponse()


class RunFlowTests(unittest.TestCase):
    """Drive main.run() with mocked scanning + downloads against a temp folder."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.pics = os.path.join(self.root, "pics")
        os.makedirs(self.pics, exist_ok=True)
        self.config_path = os.path.join(self.root, "config.json")
        self.cache_path = os.path.join(self.root, "failed_cards.json")
        self.db = os.path.join(self.root, "cards.cdb")
        with open(self.db, "w", encoding="utf-8") as f:
            f.write("placeholder")

    def _cfg(self, *extra):
        cfg = Config(["--config", self.config_path, "--quiet", "--no-field-art", *extra])
        cfg.set_edopro_path(self.root)
        return cfg

    def _seed_cache(self, ids):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump({str(cid): time.time() for cid in ids}, f)

    def _run(self, cfg, cards, outcomes):
        """outcomes: card_id -> FetchResult for every URL of that card."""

        async def fake_try_download(session, url, filepath, timeout, retries, is_valid=None):
            cid = int(os.path.splitext(os.path.basename(filepath))[0])
            result = outcomes.get(cid, main.FetchResult.MISSING)
            if result is main.FetchResult.OK:
                with open(filepath, "wb") as f:
                    f.write(b"\xff\xd8\xff" + b"\x00" * 600)
            return result

        download_mock = mock.AsyncMock(side_effect=fake_try_download)
        with (
            mock.patch.object(main, "get_db_files", return_value=[self.db]),
            mock.patch.object(main, "scan_databases", return_value=(dict(cards), {}, set(), set())),
            mock.patch.object(main, "load_manual_map", return_value={}),
            mock.patch.object(main.aiohttp, "ClientSession", FakeSession),
            mock.patch.object(main, "check_for_update", new=mock.AsyncMock(return_value=None)),
            mock.patch.object(main, "_try_download", new=download_mock),
        ):
            asyncio.run(main.run(cfg))
        return download_mock

    def _cache_keys(self):
        if not os.path.exists(self.cache_path):
            return set()
        with open(self.cache_path, encoding="utf-8") as f:
            return set(json.load(f).keys())

    def test_cached_artwork_is_not_reported_as_already_present(self):
        self._seed_cache([111])
        cfg = self._cfg()
        download = self._run(cfg, {111: "Missing card"}, {})
        download.assert_not_awaited()
        self.assertEqual(cfg.idle_summary[0], "No new artwork available")
        self.assertIn("(1)", cfg.idle_summary[1])

    def test_run_caches_only_definitive_misses_and_evicts_recovered_cards(self):
        # C is cached as missing but its art now exists on disk -> must be evicted.
        self._seed_cache([333])
        with open(os.path.join(self.pics, "333.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff" + b"\x00" * 600)

        cards = {111: "Definitive Miss", 222: "Transient Trouble", 333: "Recovered"}
        outcomes = {111: main.FetchResult.MISSING, 222: main.FetchResult.ERROR}
        self._run(self._cfg(), cards, outcomes)

        keys = self._cache_keys()
        self.assertIn("111", keys)  # 404 everywhere -> remembered
        self.assertNotIn("222", keys)  # network trouble -> retried next run
        self.assertNotIn("333", keys)  # art on disk -> evicted

    def test_deck_filtered_recheck_preserves_out_of_scope_cache(self):
        # 999 is cached but NOT in the deck; a --recheck-missing deck run
        # must leave its cache entry alone.
        self._seed_cache([999, 111])
        deck = os.path.join(self.root, "mine.ydk")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("#main\n111\n")

        cards = {111: "Deck Card", 999: "Out Of Scope"}
        outcomes = {111: main.FetchResult.MISSING}
        downloads = self._run(self._cfg("--deck", deck, "--recheck-missing"), cards, outcomes)

        keys = self._cache_keys()
        self.assertIn("999", keys)  # untouched: never re-attempted
        self.assertIn("111", keys)  # re-attempted, still missing -> recached
        # Prove the recheck actually re-attempted 111 and never touched 999.
        attempted = [call.args[2] for call in downloads.await_args_list]
        self.assertTrue(any("111" in path for path in attempted))
        self.assertFalse(any("999" in path for path in attempted))

    def test_era_upgrade_refreshes_existing_image_once(self):
        path = os.path.join(self.pics, "511000818.jpg")
        with open(path, "wb") as f:
            f.write(b"old low resolution")
        cards = {511000818: "Sinister Serpent (Pre-Errata)"}
        first = self._run(self._cfg(), cards, {511000818: main.FetchResult.OK})
        self.assertTrue(first.await_count)
        with open(self.config_path, encoding="utf-8") as f:
            applied = json.load(f)["era_art_applied"]
        folder = os.path.normcase(os.path.abspath(self.root))
        self.assertEqual(applied[folder], {"511000818": "hd-v2"})
        second = self._run(self._cfg(), cards, {})
        self.assertEqual(second.await_count, 0)
        switched = self._run(self._cfg("--original-text"), cards, {511000818: main.FetchResult.OK})
        self.assertTrue(switched.await_count)

    def test_failed_era_upgrade_preserves_existing_image_and_retries(self):
        path = os.path.join(self.pics, "511000818.jpg")
        with open(path, "wb") as f:
            f.write(b"old image")
        cards = {511000818: "Sinister Serpent (Pre-Errata)"}
        self._run(self._cfg(), cards, {511000818: main.FetchResult.ERROR})
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"old image")
        retry = self._run(self._cfg(), cards, {511000818: main.FetchResult.OK})
        self.assertTrue(retry.await_count)

    def test_deck_upgrade_leaves_other_era_cards_pending(self):
        cards = {111: "First (Pre-Errata)", 222: "Second (Pre-Errata)"}
        for cid in cards:
            with open(os.path.join(self.pics, f"{cid}.jpg"), "wb") as f:
                f.write(b"old image")
        deck = os.path.join(self.root, "mine.ydk")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("#main\n111\n")
        self._run(self._cfg("--deck", deck), cards, {111: main.FetchResult.OK})
        next_run = self._run(self._cfg(), cards, {222: main.FetchResult.OK})
        paths = {os.path.basename(call.args[2]) for call in next_run.await_args_list}
        self.assertEqual(paths, {"222.jpg"})

    def test_cancelled_era_refresh_does_not_mark_cards_applied(self):
        cfg = self._cfg()
        cfg.cancel_event = threading.Event()
        cfg.cancel_event.set()
        cards = {111: "First (Pre-Errata)"}
        self._run(cfg, cards, {111: main.FetchResult.OK})
        self.assertEqual(cfg.era_art_applied, {})
        retry = self._run(self._cfg(), cards, {111: main.FetchResult.OK})
        self.assertTrue(retry.await_count)

    def test_dry_run_leaves_era_image_and_policy_untouched(self):
        path = os.path.join(self.pics, "111.jpg")
        with open(path, "wb") as f:
            f.write(b"old image")
        self._run(self._cfg("--dry-run"), {111: "First (Pre-Errata)"}, {})
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"old image")
        self.assertFalse(os.path.exists(self.config_path))

    def test_explicit_empty_deck_filter_errors_out(self):
        """A typo'd --deck must not silently escalate into a full 13k-card sync."""
        deck = os.path.join(self.root, "empty.ydk")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("#main\n")

        cards = {111: "Only Card"}
        with self.assertRaises(SystemExit) as ctx:
            self._run(self._cfg("--deck", deck), cards, {111: main.FetchResult.OK})

        self.assertEqual(ctx.exception.code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.pics, "111.jpg")))

    def test_my_decks_with_empty_decks_reports_nothing_to_sync(self) -> None:
        deck_dir = os.path.join(self.root, "deck")
        os.makedirs(deck_dir, exist_ok=True)
        with open(os.path.join(deck_dir, "empty.ydk"), "w", encoding="utf-8") as f:
            f.write("#main\n")

        cards = {111: "Only Card"}
        cfg = self._cfg("--my-decks")
        downloads = self._run(cfg, cards, {111: main.FetchResult.OK})

        downloads.assert_not_awaited()
        self.assertEqual(cfg.idle_summary[0], "No cards found in your decks — nothing to sync")
        self.assertFalse(os.path.exists(os.path.join(self.pics, "111.jpg")))

    def test_repair_preview_preserves_card_and_field_and_reports_both(self):
        card = Path(self.pics, "111.jpg")
        field = Path(self.pics, "field", "111.png")
        field.parent.mkdir()
        for path in (card, field):
            path.write_bytes(b"broken")
        cfg = self._cfg("--dry-run", "--repair", "--field-art")
        with mock.patch.object(
            main, "scan_databases", return_value=({111: "Field"}, {}, set(), {111})
        ):
            stats = asyncio.run(main.run(cfg))
        self.assertEqual((stats.planned, stats.planned_fields), (1, 1))
        for path in (card, field):
            self.assertEqual(path.read_bytes(), b"broken")

    def test_prune_runs_on_up_to_date_library(self):
        orphan = Path(self.pics, "222.jpg")
        orphan.touch()
        Path(self.pics, "111.jpg").touch()
        downloads = self._run(self._cfg("--prune"), {111: "Card"}, {})
        downloads.assert_not_awaited()
        self.assertFalse(orphan.exists())

    def test_prune_preserves_images_if_an_expansion_failed_to_scan(self):
        Path(self.db).unlink()
        db = sqlite3.connect(self.db)
        try:
            db.executescript(
                "CREATE TABLE datas(id,type); CREATE TABLE texts(id,name);"
                "INSERT INTO datas VALUES (111,1); INSERT INTO texts VALUES (111,'Card');"
            )
        finally:
            db.close()
        expansion = Path(self.root, "expansions")
        expansion.mkdir()
        (expansion / "custom.cdb").write_bytes(b"unreadable")
        for cid in (111, 222):
            Path(self.pics, f"{cid}.jpg").touch()
        with mock.patch.object(main, "check_for_update", new=mock.AsyncMock(return_value=None)):
            asyncio.run(main.run(self._cfg("--prune")))
        self.assertTrue(Path(self.pics, "222.jpg").exists())

    def test_first_card_can_finish_while_update_check_is_waiting(self):
        downloaded = asyncio.Event()
        update_saw_download = []

        async def wait_for_card(*_):
            await asyncio.wait_for(downloaded.wait(), timeout=2)
            update_saw_download.append(True)

        async def download(*_):
            downloaded.set()
            return main.FetchResult.OK

        with (
            mock.patch.object(
                main, "scan_databases", return_value=({111: "Card"}, {}, set(), set())
            ),
            mock.patch.object(main, "check_for_update", side_effect=wait_for_card) as update,
            mock.patch.object(main, "_try_download", side_effect=download),
        ):
            stats = asyncio.run(main.run(self._cfg()))
        update.assert_awaited_once()
        self.assertEqual(update_saw_download, [True])
        self.assertEqual(stats.total_ok, 1)

    def test_prune_is_skipped_when_deck_filter_is_active(self):
        deck = os.path.join(self.root, "mine.ydk")
        with open(deck, "w", encoding="utf-8") as f:
            f.write("#main\n111\n")
        # An orphan image that a mistaken prune-under-filter would delete.
        orphan = os.path.join(self.pics, "999999.jpg")
        with open(orphan, "wb") as f:
            f.write(b"\xff\xd8\xff" + b"\x00" * 600)

        cards = {111: "Deck Card"}
        outcomes = {111: main.FetchResult.OK}
        self._run(self._cfg("--deck", deck, "--prune"), cards, outcomes)

        self.assertTrue(os.path.exists(orphan))


if __name__ == "__main__":
    unittest.main()

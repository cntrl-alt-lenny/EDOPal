import asyncio
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext, suppress
from pathlib import Path
from unittest.mock import patch

import main
from config import Config


class ScopedDiscoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "cards.cdb"
        self.deck = self.root / "example.ydk"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(
                """CREATE TABLE datas(id INTEGER PRIMARY KEY, type INTEGER);
                   CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT);
                   INSERT INTO datas VALUES (122, 1), (321, 1), (511000017, 524290);
                   INSERT INTO texts VALUES (122, 'First'), (321, 'First'),
                                            (511000017, 'First (Pre-Errata)');"""
            )

    def run_deck(self, deck_ids):
        self.deck.write_text("#main\n" + "\n".join(str(x) for x in deck_ids), encoding="utf-8")
        cfg = Config(
            [
                "--config",
                str(self.root / "preferences.json"),
                "--edopro-path",
                str(self.root),
                "--quiet",
                "--dry-run",
                "--deck",
                str(self.deck),
                "--no-textures",
                "--no-field-art",
            ]
        )
        candidates = []
        build = main.build_download_candidates

        def record(*args, **kwargs):
            result = build(*args, **kwargs)
            candidates.extend(result)
            return result

        with (
            patch.object(main, "build_download_candidates", side_effect=record),
            patch.object(main, "scan_databases", wraps=main.scan_databases) as scans,
            patch.object(main, "_create_http_session", return_value=nullcontext(None)),
            patch.object(main, "console"),
        ):
            asyncio.run(main.run(cfg))
        return scans, candidates

    def test_regular_deck_looks_up_only_its_own_ids(self):
        scans, candidates = self.run_deck([122])
        self.assertEqual(scans.call_count, 1)
        self.assertEqual(scans.call_args.kwargs["only_ids"], {122})
        self.assertEqual(candidates[0][0], "direct-id")
        self.assertTrue(candidates[0][1].endswith("/122.jpg"))

    def test_suffix_fallback_preserves_all_off_deck_artwork_candidates_in_order(self):
        scans, candidates = self.run_deck([511000017])
        self.assertEqual(scans.call_count, 2)
        self.assertNotIn("only_ids", scans.call_args.kwargs)
        self.assertEqual(
            [url.rsplit("/", 1)[1] for kind, url in candidates if kind == "name-match"],
            ["122.jpg", "321.jpg"],
        )

    def test_unknown_ids_do_not_misreport_a_healthy_database_as_corrupt(self):
        scans, candidates = self.run_deck([99, 10**80])
        self.assertEqual(scans.call_count, 2)
        self.assertEqual(candidates, [])

    def test_partial_db_scan_batches_preserve_order_and_old_schemas(self):
        secondary = self.root / "rush.delta.cdb"
        with closing(sqlite3.connect(secondary)) as conn:
            conn.executescript("""CREATE TABLE datas(id INTEGER PRIMARY KEY);
                CREATE TABLE texts(id INTEGER PRIMARY KEY, name TEXT);
                INSERT INTO datas VALUES (122);
                INSERT INTO texts VALUES (122, 'Replacement');""")
        dbs = [str(self.db), str(secondary)]
        full = main.scan_databases(dbs)
        wanted = set(range(2, 1200)) | {511000017, 10**80}
        partial = main.scan_databases(dbs, only_ids=wanted)
        self.assertEqual(partial, full)
        self.assertEqual(list(partial[0]), [122, 321, 511000017])
        self.assertEqual(partial[0][122], "Replacement")
        self.assertEqual(partial[2], {122})
        self.assertEqual(partial[3], {511000017})


class ArtDiscoveryTests(unittest.TestCase):
    def test_one_directory_pass_agrees_with_platform_path_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ("1.jpg", "2.PNG", "3.pNg", "4.txt", "wrong.jpg.zip"):
                (folder / name).touch()
            (folder / "5.jpg").mkdir()
            # Windows may require an elevated token to create symlinks.
            with suppress(OSError):
                (folder / "6.png").symlink_to(folder / "missing")
            wanted = {
                f"{i}.{ext}"
                for i in range(1, 7)
                for ext in ("jpg", "png")
                if os.path.exists(folder / f"{i}.{ext}")
            }
            self.assertEqual(main._present_art_filenames(tmp, (".jpg", ".png")), wanted)
            self.assertEqual(main._present_art_filenames(str(folder / "missing"), (".jpg",)), set())

    def test_bounded_image_reads_match_full_file_validators(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample"
            examples = [
                main.JPEG_MAGIC + b"x" * 400 + main.JPEG_END,
                main.JPEG_MAGIC + b"x" * 2000 + main.JPEG_END + b"x" * 62,
                main.JPEG_MAGIC + b"x" * 2000 + main.JPEG_END + b"x" * 63,
                main.PNG_MAGIC + b"x" * 9000 + b"IEND" + b"x" * 28,
                main.PNG_MAGIC + b"x" * 9000 + b"IEND" + b"x" * 29,
                b"html" + b"x" * 900 + main.JPEG_END,
            ]
            for content in examples:
                path.write_bytes(content)
                sample = main._image_sample(str(path))
                self.assertLessEqual(len(sample), main.MIN_IMAGE_BYTES + 64)
                for check in (main._looks_like_jpeg, main._looks_like_image):
                    self.assertEqual(check(sample), check(content))

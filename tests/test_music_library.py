import hashlib
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import music_import
import music_library as music


def audio(path, note):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 16000, 0, "NONE", ""))
        f.writeframes(int(note).to_bytes(2, "little", signed=True) * 1600)
    return path


class Soundtracks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.game = base / "Game"
        self.game.mkdir()
        self.library = base / "Library"
        self.album = base / "Album"

    def test_all_covers_have_launchbox_provenance_and_native_sizes(self):
        items = json.loads((music.ART / "catalog.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(items), 25)
        for row in items:
            with self.subTest(row["id"]):
                self.assertTrue(row["cover_source"].startswith("https://images.launchbox-app.com/"))
                self.assertTrue(row["cover_page"].startswith("https://gamesdb.launchbox-app.com/"))
                self.assertIn("Box - Front Image", row["cover_description"])
                for variant, box in (
                    ("", (400, 416)),
                    ("detail", (200, 208)),
                    ("grid", (134, 140)),
                    ("thumb", (100, 104)),
                ):
                    header = (music.ART / variant / (row["id"] + ".png")).read_bytes()[:24]
                    self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
                    width, height = struct.unpack(">II", header[16:24])
                    self.assertTrue(0 < width <= box[0] and 0 < height <= box[1])

    def test_world_championship_search_and_existing_short_filenames(self):
        games = music.catalog()
        for year, key in enumerate(
            ("wc2004", "trials", "ultimate", "wc2007", "wc2008", "wc2009", "wc2010", "wc2011"),
            start=2004,
        ):
            with self.subTest(year):
                short = (
                    f"Championship Tournament {year}" if year <= 2006 else f"Championship {year}"
                )
                full = f"World {short}"
                self.assertIn(full, (games[key].title, *games[key].aliases))
                self.assertEqual(music._identify(f"{short} - Deck.wav", games), key)
                self.assertEqual(music._identify(f"{full} - Duel.flac", games), key)

    def test_import_and_reimport_with_changed_recording(self):
        source = audio(self.album / "Theme.wav", 1)
        dst = music.import_track(source, "wc2011", "deck", root=self.library)
        self.assertEqual(music.import_track(source, "wc2011", "deck", root=self.library), dst)
        audio(source, 2)
        next_file = music.import_track(source, "wc2011", "deck", root=self.library)
        self.assertNotEqual(next_file, dst)
        self.assertNotEqual(next_file.read_bytes(), dst.read_bytes())
        self.assertEqual(len(music.tracks(self.game, "deck", root=self.library)), 2)
        with self.assertRaises(ValueError):
            music.import_track(source, "../", "deck", root=self.library)
        with self.assertRaises(ValueError):
            music.import_track(source, "wc2011", "../", root=self.library)

    def test_invalid_audio_is_rejected_before_adding(self):
        self.album.mkdir()
        bad = self.album / "Bad.flac"
        bad.write_text("Not an audio file")
        with self.assertRaises(ValueError):
            music.import_track(bad, "wc2011", "deck", root=self.library)
        self.assertFalse(self.library.exists())

    def test_duel_uses_both_game_folders_and_leaves_other_scenes_in_place(self):
        bgm = self.game / "sound/BGM"
        loose = audio(bgm / "Loose.wav", 100)
        pooled = audio(bgm / "duel/Battle.wav", 200)
        deck = audio(bgm / "deck/WC2011 - Deck Construction.wav", 300)
        other_data = deck.read_bytes()
        one = music.Track("tf5", "New duel", audio(self.album / "One.wav", 5), "duel")
        two = music.Track("spirit", "Other duel", audio(self.album / "Two.wav", 6), "duel")
        before = {p: p.read_bytes() for p in (loose, pooled)}
        self.assertFalse(music.can_restore(self.game, "duel"))
        music.use_track(self.game, one)
        self.assertEqual(deck.read_bytes(), other_data)
        installed = list((bgm / "duel").glob("*.wav"))
        self.assertEqual([x.read_bytes() for x in installed], [one.path.read_bytes()])
        self.assertFalse(loose.exists())
        library = music.tracks(self.game, "duel", root=self.library)
        self.assertEqual([t.title for t in library if t.in_playlist], ["New duel"])
        self.assertIn("Battle", [t.title for t in library])
        music.use_track(self.game, one)
        music.use_track(self.game, two)
        extra = audio(bgm / "duel/Added after selection.wav", 77)
        music.restore_playlist(self.game, "duel")
        self.assertFalse(music.can_restore(self.game, "duel"))
        self.assertTrue(extra.exists())
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual({p.name for p in (bgm / "duel").glob("*.wav")}, {pooled.name, extra.name})

    def test_active_local_track_can_itself_be_selected(self):
        source = audio(self.game / "sound/BGM/deck/WC2011 - Deck Construction.wav", 123)
        original = source.read_bytes()
        (track,) = music.tracks(self.game, "deck", root=self.library)
        self.assertEqual(track.game, "wc2011")
        music.use_track(self.game, track)
        self.assertFalse(source.exists())
        music.restore_playlist(self.game, "deck")
        self.assertEqual(source.read_bytes(), original)

    def test_restore_collision_rolls_back_and_keeps_both_copies(self):
        original = audio(self.game / "sound/BGM/deck/Existing.wav", 1)
        previous = original.read_bytes()
        music.use_track(
            self.game, music.Track("wc2007", "Deck", audio(self.album / "Song.wav", 2), "deck")
        )
        modified = audio(original, 3).read_bytes()
        with self.assertRaises(FileExistsError):
            music.restore_playlist(self.game, "deck")
        self.assertEqual(original.read_bytes(), modified)
        self.assertTrue(music.can_restore(self.game, "deck"))
        self.assertEqual(len(list(original.parent.glob("*.wav"))), 2)
        original.rename(original.with_name("Third-party update.wav"))
        music.restore_playlist(self.game, "deck")
        self.assertEqual(original.read_bytes(), previous)

    def test_modified_managed_file_is_never_removed(self):
        music.use_track(
            self.game, music.Track("wc2010", "Duel", audio(self.album / "A.wav", 2), "duel")
        )
        (managed,) = (self.game / "sound/BGM/duel").glob("*.wav")
        modified = audio(managed, 7).read_bytes()
        music.restore_playlist(self.game, "duel")
        self.assertEqual(managed.read_bytes(), modified)

    def test_failure_mid_selection_preserves_originals(self):
        existing = audio(self.game / "sound/BGM/deck/Original.wav", 9)
        source = audio(self.album / "A.wav", 2)
        track = music.Track("wc2010", "Deck", source, "deck")
        real_move = music._move

        def fail_second(src, dest, changes):
            if len(changes) == 1:
                raise PermissionError("Open in another app")
            return real_move(src, dest, changes)

        with (
            patch("music_library._move", side_effect=fail_second),
            self.assertRaises(PermissionError),
        ):
            music.use_track(self.game, track)
        self.assertTrue(existing.exists())
        self.assertFalse(music.can_restore(self.game, "deck"))

    def test_restore_recovers_an_interrupted_publish(self):
        directory = self.game / "sound/BGM/deck"
        source = audio(directory / "Song.wav", 3)
        before = source.read_bytes()
        state = self.game / "sound/.edopro-hd-sync/deck"
        backup = state / "saved/deck"
        backup.mkdir(parents=True)
        source.rename(backup / source.name)
        new = audio(self.album / "New.wav", 7)
        digest = hashlib.sha256(new.read_bytes()).hexdigest()
        name = f"HD Sync - spirit - {digest[:16]}.wav"
        (directory / name).write_bytes(new.read_bytes())
        music._write_pending(
            state, {"game": "spirit", "digest": digest, "name": name, "title": "New"}
        )
        self.assertTrue(music.can_restore(self.game, "deck"))
        with self.assertRaisesRegex(OSError, "interrupted"):
            music.use_track(self.game, music.Track("spirit", "New", new, "deck"))
        music.restore_playlist(self.game, "deck")
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((directory / name).exists())
        self.assertFalse(music.can_restore(self.game, "deck"))

    def test_music_setting_hint(self):
        conf = self.game / "config/system.conf"
        conf.parent.mkdir()
        conf.write_bytes(b"volume_music = 17\r\nenable_music = 0\r\n")
        self.assertTrue(music.music_disabled(self.game))
        conf.write_text("enable_music = 1\n")
        self.assertFalse(music.music_disabled(self.game))

    def test_music_setting_change_and_undo_preserve_file_bytes(self):
        conf = self.game / "config/system.conf"
        conf.parent.mkdir()
        original = b"\xef\xbb\xbfenable_music = 1\r\n# note\r\n  enable_music\t= 0 # user\r\nmusic_volume = 17"
        conf.write_bytes(original)
        with patch("music_library._game_running", return_value=False):
            self.assertFalse(music.music_enabled(self.game))
            music.set_music_enabled(self.game, True)
            self.assertEqual(conf.read_bytes(), original.replace(b"= 0 # user", b"= 1 # user"))
            self.assertTrue(music.music_enabled(self.game))
            music.set_music_enabled(self.game, False)
        self.assertEqual(conf.read_bytes(), original)
        with (
            patch("music_library._game_running", return_value=True),
            self.assertRaisesRegex(OSError, "Close"),
        ):
            music.set_music_enabled(self.game, True)
        self.assertEqual(conf.read_bytes(), original)

    def test_missing_or_unknown_music_flag_is_not_synthesized(self):
        conf = self.game / "config/system.conf"
        conf.parent.mkdir()
        conf.write_bytes(b"music_volume = 17")
        self.assertIsNone(music.music_enabled(self.game))
        with (
            patch("music_library._game_running", return_value=False),
            self.assertRaises(ValueError),
        ):
            music.set_music_enabled(self.game, True)
        self.assertEqual(conf.read_bytes(), b"music_volume = 17")

    def test_import_collection_preserves_games_scenes_and_existing_files(self):
        source = self.album / "soundtracks"
        audio(source / "tf5/deck/Calm Place.wav", 1)
        audio(source / "wc2010/duel/Battle.wav", 2)
        audio(source / "unrelated/duel/Ignore.wav", 3)
        self.assertEqual(music_import.import_library(source, root=self.library), (2, 0))
        self.assertEqual(music_import.import_library(source, root=self.library), (0, 2))
        audio(source / "tf5/deck/Calm Place.wav", 9)
        self.assertEqual(music_import.import_library(source, root=self.library), (1, 1))
        self.assertTrue((self.library / "tf5/deck/Calm Place (2).wav").exists())
        self.assertFalse((self.library / "unrelated").exists())

    def test_import_power_of_chaos_voice_archive_and_trilogy_zip(self):
        data = audio(self.album / "raw.wav", 100).read_bytes()
        name = b"joey\\english\\stream\\m_duel1.wav"
        entry = bytes((b >> 4 | b << 4) & 255 for b in name).ljust(256, b"\0") + struct.pack(
            "<III", 280, len(data), len(data)
        )
        archive = self.album / "Voice.dat"
        archive.write_bytes(b"KCEJYUGI" + struct.pack("<I", 1) + entry + data)
        self.assertEqual(music_import.import_power_of_chaos(archive, root=self.library), (1, 0))
        self.assertEqual((self.library / "pocjoey/duel/Duel 1.wav").read_bytes(), data)
        zipped = self.album / "trilogy.zip"
        with ZipFile(zipped, "w") as z:
            z.write(archive, "3 - Joey the Passion/Voice.dat")
            z.writestr("../../not-an-audio-file", "ignore")
        self.assertEqual(music_import.import_power_of_chaos(zipped, root=self.library), (0, 1))
        archive.write_bytes(archive.read_bytes()[:-40])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            music_import.import_power_of_chaos(archive, root=self.library)

    def test_resolve_bundled_track_while_custom_duel_selection_is_active(self):
        live = audio(self.game / "sound/BGM/duel/False Shuffle.flac.wav", 11)
        with music.playlist_lock(self.game, "duel"):
            self.assertEqual(music.original_duel_track_path(self.game, live.name), live)
        music.use_track(
            self.game, music.Track("tf5", "Duel", audio(self.album / "Duel.wav", 5), "duel")
        )
        with music.playlist_lock(self.game, "duel"):
            saved = music.original_duel_track_path(self.game, live.name)
            self.assertTrue(saved.exists())
            self.assertNotEqual(saved, live)
            audio(live, 21)
            with self.assertRaises(OSError):
                music.original_duel_track_path(self.game, live.name)

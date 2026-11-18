import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import extras
import music_library


class AppearanceUndoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.game = root / "Game"
        self.game.mkdir()
        self.config = str(root / "Config" / "config.json")
        self.player = self.game / "textures/cover.png"
        self.player.parent.mkdir()
        self.opponent = self.player.with_name("cover2.png")

    def apply(self, **options):
        extras.apply_appearance(str(self.game), self.config, **options)

    def undo(self, group):
        self.apply(undo_group=group)

    def available(self, group):
        return extras.can_undo(str(self.game), self.config, group)

    def test_undo_previous_files_and_only_new_file_removed(self):
        self.player.write_bytes(b"custom player")
        neighbour = self.player.with_name("custom.png")
        neighbour.write_bytes(b"unrelated")
        self.apply(player_sleeve="1998 OCG", opponent_sleeve="Vortex")
        self.apply(player_sleeve="1998 OCG", opponent_sleeve="Vortex")
        self.assertTrue(self.available("sleeves"))
        self.undo("sleeves")
        self.assertEqual(self.player.read_bytes(), b"custom player")
        self.assertFalse(self.opponent.exists())
        self.assertEqual(neighbour.read_bytes(), b"unrelated")
        self.assertFalse(self.available("sleeves"))

    def test_active_skin_sleeve_overrides_and_jpeg_fallback(self):
        skin = self.game / "skin/Indigo/textures"
        skin.mkdir(parents=True)
        (skin / "cover.jpg").write_bytes(b"original JPEG")
        self.player.write_bytes(b"base sleeve")
        settings = self.game / "config/system.conf"
        settings.parent.mkdir()
        settings.write_text("skin = Indigo\n")
        self.apply(player_sleeve="Vortex")
        self.assertTrue((skin / "cover.png").is_file())
        self.assertEqual(self.player.read_bytes(), b"base sleeve")
        settings.write_text("skin = Other\n")
        self.undo("sleeves")
        self.assertFalse((skin / "cover.png").exists())
        self.assertEqual((skin / "cover.jpg").read_bytes(), b"original JPEG")

    def test_external_edit_refuses_whole_undo_and_can_be_kept_as_new_original(self):
        self.player.write_bytes(b"custom player")
        self.apply(player_sleeve="1998 OCG", opponent_sleeve="Vortex")
        installed = self.player.read_bytes()
        self.opponent.write_bytes(b"hand-edited opponent")
        with self.assertRaisesRegex(ValueError, "changed after installation"):
            self.undo("sleeves")
        self.assertEqual(self.player.read_bytes(), installed)
        self.assertTrue(self.available("sleeves"))
        self.apply(opponent_sleeve="1998 OCG")
        self.undo("sleeves")
        self.assertEqual(self.opponent.read_bytes(), b"hand-edited opponent")
        self.assertEqual(self.player.read_bytes(), installed)

    def test_replace_24_effects_then_undo_ten_synth_effects_only(self):
        self.apply(effects="Thonk")
        sound = self.game / "sound"
        thonk = {file.name: file.read_bytes() for file in sound.glob("*.wav")}
        self.apply(effects="Synth")
        self.assertNotEqual((sound / "draw.wav").read_bytes(), thonk["draw.wav"])
        self.undo("effects")
        self.assertEqual({file.name: file.read_bytes() for file in sound.glob("*.wav")}, thonk)

    def test_interrupted_install_and_undo_resume_after_process_exit(self):
        self.player.write_bytes(b"before")
        original_write = extras._atomic_write

        def interrupt(path, data):
            if path == self.opponent:
                raise KeyboardInterrupt
            original_write(path, data)

        with (
            patch.object(extras, "_atomic_write", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.apply(player_sleeve="1998 OCG", opponent_sleeve="Vortex")
        self.assertNotEqual(self.player.read_bytes(), b"before")
        self.assertTrue(self.available("sleeves"))
        self.undo("sleeves")
        self.assertEqual(self.player.read_bytes(), b"before")
        self.assertFalse(self.opponent.exists())
        self.opponent.write_bytes(b"opponent before")
        self.apply(player_sleeve="1998 OCG", opponent_sleeve="Vortex")
        with (
            patch.object(extras, "_atomic_write", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.undo("sleeves")
        self.assertTrue(self.available("sleeves"))
        self.undo("sleeves")
        self.assertEqual(self.player.read_bytes(), b"before")
        self.assertEqual(self.opponent.read_bytes(), b"opponent before")

    def test_disk_error_rolls_back_immediately(self):
        self.player.write_bytes(b"before")
        original_write = extras._atomic_write

        def fail(path, data):
            if path == self.opponent:
                raise OSError("Disk full")
            original_write(path, data)

        with (
            patch.object(extras, "_atomic_write", side_effect=fail),
            self.assertRaisesRegex(OSError, "Disk full"),
        ):
            self.apply(player_sleeve="1998 OCG", opponent_sleeve="Vortex")
        self.assertEqual(self.player.read_bytes(), b"before")
        self.assertFalse(self.opponent.exists())
        self.assertFalse(self.available("sleeves"))

    def test_synth_undo_follows_parked_original_across_playlist_switch(self):
        sound = self.game / "sound/BGM/duel"
        sound.mkdir(parents=True)
        source = extras.ASSETS / "sound/draw.wav"
        custom = sound / "custom.wav"
        custom.write_bytes(source.read_bytes())
        music_library.use_track(self.game, music_library.Track("local", "Custom", custom, "duel"))
        selected = next(sound.glob("HD Sync*"))
        contents = selected.read_bytes()
        self.apply(synth_music=True)
        self.assertFalse((sound / "False Shuffle.flac").exists())
        self.assertTrue((self.game / "sound/BGM/menu/Glass Archive.flac").exists())
        self.assertEqual(selected.read_bytes(), contents)
        music_library.restore_playlist(self.game, "duel")
        self.assertTrue((sound / "False Shuffle.flac").is_file())
        self.undo("music")
        self.assertFalse((sound / "False Shuffle.flac").exists())
        self.assertFalse((self.game / "sound/BGM/menu/Glass Archive.flac").exists())
        self.assertEqual(custom.read_bytes(), contents)

    def test_unknown_manifest_paths_and_linked_files_are_preserved(self):
        self.apply(player_sleeve="Vortex")
        unrelated = self.player.with_name("unrelated.png")
        unrelated.write_bytes(b"keep")
        state = extras._group_dir(self.game, self.config, "sleeves") / "last.json"
        original = state.read_bytes()
        state.write_text(
            json.dumps({"textures/unrelated.png": [None, hashlib.sha256(b"keep").hexdigest()]})
        )
        with self.assertRaisesRegex(ValueError, "unrecognised file"):
            self.undo("sleeves")
        self.assertEqual(unrelated.read_bytes(), b"keep")
        state.write_bytes(original)
        self.player.unlink()
        external = self.game.parent / "outside.png"
        external.write_bytes(b"keep outside")
        try:
            self.player.symlink_to(external)
        except OSError:
            self.skipTest("Symbolic link creation not available")
        with self.assertRaises(OSError):
            self.undo("sleeves")
        self.assertEqual(external.read_bytes(), b"keep outside")
        self.assertTrue(self.player.is_symlink())

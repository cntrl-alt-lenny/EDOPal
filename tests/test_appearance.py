import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import extras


class AppearanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "ProjectIgnis"
        self.config = str(Path(folder.name) / "state/config.json")
        self.conf = self.root / "config/system.conf"
        self.conf.parent.mkdir(parents=True)

    def apply(self, **kwargs):
        extras.apply_appearance(str(self.root), self.config, **kwargs)

    def test_each_skin_installs_and_restores_the_same_original_settings(self):
        original = (
            b"\xef\xbb\xbfskin = Indigo\r\n# notes\r\nvolume = 40\r\ntextfont = my-font.ttf 16\r\n"
        )
        self.conf.write_bytes(original)
        for name in extras.SKINS:
            with self.subTest(skin=name):
                self.apply(skin=name)
                self.assertEqual(extras.current_skin(str(self.root)), "Monokai " + name)
                folder = self.root / "skin" / ("Monokai " + name)
                self.assertTrue((folder / "skin.xml").is_file())
                self.assertEqual(
                    (folder / "textures/bg.png").read_bytes(),
                    (folder / "textures/bg_duel_topdown.png").read_bytes(),
                )
        self.assertTrue((self.root / "fonts/Inter-OFL.txt").is_file())
        self.conf.write_bytes(self.conf.read_bytes().replace(b"volume = 40", b"volume = 25"))
        self.apply(restore=True)
        self.assertEqual(self.conf.read_bytes(), original.replace(b"volume = 40", b"volume = 25"))
        self.assertFalse(extras.can_restore(str(self.root), self.config))
        self.assertFalse((self.root / "sound").exists())

    def test_sleeves_and_audio_preserve_skin_and_unselected_groups(self):
        self.conf.write_text("skin = Custom\n")
        self.apply(sleeves=True)
        self.assertTrue((self.root / "textures/cover2.png").is_file())
        self.assertFalse((self.root / "sound").exists())
        self.apply(audio="Thonk")
        self.assertEqual(len(list((self.root / "sound").glob("*.wav"))), 24)
        self.assertFalse((self.root / "sound/BGM").exists())
        self.assertEqual(self.conf.read_text(), "skin = Custom\n")
        self.assertFalse(extras.can_restore(str(self.root), self.config))

    def test_each_slot_can_use_either_original_sleeve(self):
        self.apply(player_sleeve="1998 OCG")
        self.assertFalse((self.root / "textures/cover2.png").exists())
        self.apply(opponent_sleeve="Vortex")
        with ZipFile(extras.PACKS / "shared.zip") as archive:
            self.assertEqual(
                (self.root / "textures/cover.png").read_bytes(), archive.read("textures/cover2.png")
            )
            self.assertEqual(
                (self.root / "textures/cover2.png").read_bytes(), archive.read("textures/cover.png")
            )

    def test_effects_and_background_music_install_independently(self):
        self.apply(effects="Synth")
        self.assertEqual(len(list((self.root / "sound").glob("*.wav"))), 10)
        self.assertFalse((self.root / "sound/BGM").exists())
        self.apply(effects="Thonk", synth_music=True)
        self.assertEqual(len(list((self.root / "sound").glob("*.wav"))), 24)
        self.assertEqual(len(list((self.root / "sound/BGM").rglob("*.flac"))), 2)
        self.assertFalse((self.root / "sound/BGM/deck").exists())

    def test_turn_highlights_are_optional_and_restore_each_original_xml(self):
        for skin in extras.SKINS:
            with self.subTest(skin=skin), ZipFile(extras.PACKS / (skin.lower() + ".zip")) as pack:
                name = f"skin/Monokai {skin}/skin.xml"
                source = pack.read(name)
                self.assertEqual([rgba[0] for rgba in extras._turn_colors(source)], [0, 0])
                self.apply(skin=skin, highlight_turn=True)
                self.assertTrue(extras.turn_highlight_enabled(self.root, skin))
                self.assertEqual(
                    extras._turn_colors((self.root / name).read_bytes()), extras.TURN_ACCENTS[skin]
                )
                self.apply(skin=skin, highlight_turn=False)
                self.assertEqual((self.root / name).read_bytes(), source)

    def test_toggling_active_skin_preserves_local_preferences(self):
        self.apply(skin="Moon")
        xml = self.root / "skin/Monokai Moon/skin.xml"
        original = xml.read_bytes().replace(b'<Version data="3.0"', b'<Version data="local"')
        xml.write_bytes(original)
        settings = self.conf.read_bytes().replace(b"InterEDO-Regular.ttf 12", b"Other.ttf 17")
        self.conf.write_bytes(settings)
        self.apply(skin="Moon", highlight_turn=True)
        self.assertTrue(extras.turn_highlight_enabled(self.root, "Moon"))
        self.assertIn(b'Version data="local"', xml.read_bytes())
        self.assertEqual(self.conf.read_bytes(), settings)
        self.apply(skin="Moon", highlight_turn=False)
        self.assertEqual(xml.read_bytes(), original)
        self.assertEqual(self.conf.read_bytes(), settings)

    def test_highlight_requires_a_known_skin(self):
        with self.assertRaises(ValueError):
            self.apply(highlight_turn=True)
        with self.assertRaises(ValueError):
            self.apply(skin="Other", highlight_turn=True)

    def test_incomplete_install_never_activates_partial_skin(self):
        self.conf.write_text("skin = Custom\n")
        with tempfile.TemporaryDirectory() as empty, patch.object(extras, "PACKS", Path(empty)):
            with ZipFile(Path(empty) / "moon.zip", "w") as archive:
                archive.writestr("skin/Monokai Moon/skin.xml", "<Skin/>")
                archive.writestr("duplicates.json", json.dumps({}))
            with self.assertRaises(FileNotFoundError):
                self.apply(skin="Moon")
        self.assertEqual(self.conf.read_text(), "skin = Custom\n")
        self.assertFalse(extras.can_restore(str(self.root), self.config))

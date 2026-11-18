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

    def test_restore_keeps_later_settings_and_recovers_individual_assets(self) -> None:
        self.conf.write_text("skin = Indigo\ntextfont = Original\n")
        folder = self.root / "skin/Monokai Moon"
        folder.mkdir(parents=True)
        xml = folder / "skin.xml"
        xml.write_bytes(b"original xml")
        self.apply(skin="Moon")
        background = folder / "textures/bg.png"
        background.write_bytes(b"player artwork")
        self.conf.write_text("skin = Later\ntextfont = Player\nnumfont = fonts/Inter-Medium.ttf\n")
        self.apply(restore=True)
        self.assertEqual(self.conf.read_text(), "skin = Later\ntextfont = Player\n")
        self.assertEqual(xml.read_bytes(), b"original xml")
        self.assertEqual(background.read_bytes(), b"player artwork")
        self.assertFalse((self.root / "fonts/Inter-Medium.ttf").exists())

    def test_first_failed_pack_publication_has_recoverable_originals(self) -> None:
        original = extras._atomic_write
        writes = []

        def interrupt(path: Path, data: bytes) -> None:
            if path.is_relative_to(self.root):
                if writes:
                    raise PermissionError("simulated interruption")
                writes.append(path)
            original(path, data)

        with patch.object(extras, "_atomic_write", interrupt), self.assertRaises(PermissionError):
            self.apply(skin="Moon")
        self.assertTrue(extras.can_restore(str(self.root), self.config))
        self.apply(restore=True)
        self.assertFalse(writes[0].exists())

    def test_zip_directory_checked_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(extras, "PACKS", Path(folder)):
            (Path(folder) / "moon.zip").write_bytes(b"not a zip")
            with (
                patch.object(extras, "ZipFile", side_effect=AssertionError("directory allocated")),
                self.assertRaises(ValueError),
            ):
                self.apply(skin="Moon")

    def test_legacy_receipt_restores_only_recognizable_pack_settings(self) -> None:
        backup = extras._backup_dir(self.root, self.config)
        backup.mkdir(parents=True)
        (backup / "prior-skin.json").write_text(
            json.dumps({"skin": "Old", "textfont": "Original", "numfont": None})
        )
        self.conf.write_text(
            "skin = Newer personal skin\ntextfont = Other\nnumfont = fonts/Inter-Medium.ttf\nvolume = 10\n"
        )
        self.apply(restore=True)
        self.assertEqual(
            self.conf.read_text(), "skin = Newer personal skin\ntextfont = Other\nvolume = 10\n"
        )

    def test_restore_leaves_earlier_pack_selected_by_player(self) -> None:
        self.conf.write_text("skin = Personal\n")
        self.apply(skin="Moon")
        self.apply(skin="Abyss")
        self.conf.write_bytes(self.conf.read_bytes().replace(b"Monokai Abyss", b"Monokai Moon"))
        self.apply(restore=True)
        self.assertEqual(extras.current_skin(str(self.root)), "Monokai Moon")
        self.assertTrue((self.root / "skin/Monokai Moon/skin.xml").is_file())

    def test_restore_keeps_font_used_by_later_font_size_choice(self) -> None:
        self.apply(skin="Moon")
        self.conf.write_bytes(self.conf.read_bytes().replace(b"Regular.ttf 12", b"Regular.ttf 19"))
        self.apply(restore=True)
        self.assertIn(b"Regular.ttf 19", self.conf.read_bytes())
        self.assertTrue((self.root / "fonts/InterEDO-Regular.ttf").is_file())

    def test_oversized_skin_definition_rejected_before_member_read(self) -> None:
        archive_path = self.root / "oversized.zip"
        name = "skin/Monokai Moon/skin.xml"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr(name, b"x" * (1024**2 + 1))
        with (
            patch.object(ZipFile, "read", side_effect=AssertionError("oversized member read")),
            self.assertRaisesRegex(ValueError, "too large"),
        ):
            extras._install_pack(
                self.root,
                self.root / "backup",
                "Moon",
                "skin/Monokai Moon/",
                archive_path=archive_path,
            )

    def test_oversized_active_definition_is_not_read_to_detect_highlight(self) -> None:
        xml = self.root / "skin/Monokai Moon/skin.xml"
        xml.parent.mkdir(parents=True)
        xml.write_bytes(b"x" * (1024**2 + 1))
        with patch.object(Path, "read_bytes", side_effect=AssertionError("oversized XML read")):
            self.assertFalse(extras.turn_highlight_enabled(self.root, "Moon"))

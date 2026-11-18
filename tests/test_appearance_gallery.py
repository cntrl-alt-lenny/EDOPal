"""UI regression checks for gallery scope and the actual bundled preview set."""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

import extras

try:
    import tkinter as tk

    from appearance_gallery import ART, VIEWS, AppearanceGallery
except ImportError:
    tk = None


@unittest.skipIf(tk is None, "Tk unavailable")
class GalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest("No Tk display") from exc
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.game = Path(tmp.name) / "ProjectIgnis"
        self.game.mkdir()
        self.applied = []
        self.gallery = AppearanceGallery(
            self.root,
            family="TkDefaultFont",
            back=lambda: None,
            apply=self.applied.append,
            open_music=lambda **_kwargs: None,
            on_layout=lambda: None,
        )
        self.addCleanup(self.gallery.destroy)
        self.gallery.open(str(self.game), str(self.game.parent / "settings.json"))

    def tab(self, name):
        self.gallery.tabs[name].invoke()

    def test_preview_all_real_game_views_without_applying(self):
        for skin in extras.SKINS:
            self.gallery.tiles[skin].invoke()
            for title, slug in VIEWS.items():
                self.gallery.scene_buttons[title].invoke()
                prefix = "compact/" if self.gallery.compact else ""
                self.assertEqual(
                    self.gallery.preview.cget("image")[0],
                    str(self.gallery._image(f"{prefix}{skin.lower()}-{slug}")),
                )
                image = ART / "large" / f"{skin.lower()}-{slug}.png"
                self.assertGreaterEqual(struct.unpack(">I", image.read_bytes()[16:20])[0], 850)
        self.assertEqual(self.applied, [])
        self.gallery.apply_button.invoke()
        self.assertEqual(self.applied, [{"skin": "Ristretto", "highlight_turn": False}])

    def test_highlight_previews_only_the_selected_skin_before_applying(self):
        self.gallery.tiles["Moon"].invoke()
        self.gallery.turn_option.invoke()
        self.assertEqual(self.gallery.view.get(), "Duel field")
        self.assertEqual(self.gallery._preview_key(), "moon-turn")
        self.assertEqual(self.applied, [])
        self.gallery.tiles["Abyss"].invoke()
        self.assertEqual(self.gallery._preview_key(), "abyss-duel")
        self.gallery.tiles["Moon"].invoke()
        self.assertEqual(self.gallery._preview_key(), "moon-turn")
        self.gallery.apply_button.invoke()
        self.assertEqual(self.applied, [{"skin": "Moon", "highlight_turn": True}])

    def test_only_the_active_category_is_applied(self):
        self.gallery.tiles["Moon"].invoke()
        self.tab("Card sleeves")
        self.assertTrue(self.gallery.apply_button.instate(["disabled"]))
        self.gallery.sleeves[1].set("Vortex")
        self.gallery._sleeves_changed()
        self.gallery.apply_button.invoke()
        self.tab("Duel effects")
        self.gallery.pack_buttons["Synth"].invoke()
        self.gallery.apply_button.invoke()
        self.tab("Music")
        self.gallery._apply_selection()
        self.assertEqual(
            self.applied,
            [{"opponent_sleeve": "Vortex"}, {"effects": "Synth"}, {"synth_music": True}],
        )

    def test_swapped_sleeves_identified_and_different_folder_refreshed(self):
        textures = self.game / "textures"
        textures.mkdir()
        with ZipFile(extras.PACKS / "shared.zip") as pack:
            for out, name in (("cover", "1998 OCG"), ("cover2", "Vortex")):
                (textures / (out + ".png")).write_bytes(pack.read(extras.SLEEVES[name]))
        self.tab("Card sleeves")
        self.assertEqual(
            [label.cget("text") for label in self.gallery.sleeve_labels],
            ["Installed · 1998 OCG", "Installed · Vortex"],
        )
        other = self.game.parent / "Other installation"
        other.mkdir()
        self.gallery.open(str(other), self.gallery.config_path)
        self.tab("Card sleeves")
        self.assertEqual(
            [label.cget("text") for label in self.gallery.sleeve_labels],
            ["Installed · Game default", "Follows your sleeve"],
        )

    def test_busy_lock_and_retry_keep_sleeve_selection(self):
        self.tab("Card sleeves")
        self.gallery.sleeves[0].set("1998 OCG")
        self.gallery._sleeves_changed()
        self.gallery.set_busy("Applying…")
        self.assertTrue(all(w.instate(["disabled"]) for w in self.gallery.controls))
        self.gallery.finish(False, "Read-only installation")
        self.assertEqual(self.gallery.status.get(), "Read-only installation")
        self.gallery.apply_button.invoke()
        self.assertEqual(self.applied, [{"player_sleeve": "1998 OCG"}])

    def test_audition_only_plays_a_local_cue(self):
        self.tab("Duel effects")
        with mock.patch("music_player.PreviewPlayer") as player:
            self.gallery.listen_buttons["Thonk"].invoke()
            path = player.return_value.play.call_args.args[0]
            self.assertEqual(path.read_bytes()[:4], b"RIFF")
            self.tab("Music")
            player.return_value.stop.assert_called()
        self.assertEqual(self.applied, [])


if __name__ == "__main__":
    unittest.main()

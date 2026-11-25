import json
import xml.etree.ElementTree as ET
from concurrent.futures import CancelledError
from contextlib import contextmanager
from threading import Event

import pytest
from PIL import Image

from edopal.core.jobs import JobContext
from edopal.core.safe_storage import SafeStorage
from edopal.features.themes.service import ThemeService

VORTEX = "cardback-vortex-classic"
OCG = "cardback-ocg-1998"
ANIME = "cardback-vortex-anime"


def settings(root):
    entries = (root / "config/system.conf").read_text(encoding="utf-8-sig").splitlines()
    settings = {}
    for entry in entries:
        key, equal, value = entry.partition("=")
        if equal:
            settings[key.strip()] = value.strip()
    return settings


def test_default_choices_and_installation_in_a_single_undo(core, packs):
    game = core.installs.current().root
    conf = game / "config/system.conf"
    before = conf.read_bytes()
    theme = ThemeService(core)
    initial = theme.state()
    assert initial.active_theme is None
    assert initial.cardbacks == {"you": VORTEX, "opponent": OCG}
    assert not initial.can_undo

    progress = []
    state = theme.apply_theme(packs["theme-abyss"], JobContext(lambda done, total, message: progress.append((done, total))))
    assert state.active_theme == "theme-abyss"
    assert state.can_undo and state.has_font
    assert any(total > 0 and done == total for done, total in progress)
    cached = core.packs.cached("theme-abyss")
    skin = game / "skin/EDOPal-abyss"
    assert (skin / "skin.xml").read_bytes() == (cached / "skin/skin.xml").read_bytes()
    assert (skin / "textures/bg_menu.png").read_bytes() == (cached / "skin/textures/bg_menu.png").read_bytes()
    assert (skin / "textures/cover.png").read_bytes() == (core.packs.cached(VORTEX) / "back.png").read_bytes()
    assert (skin / "textures/cover2.png").read_bytes() == (core.packs.cached(OCG) / "back.png").read_bytes()
    assert not (skin / "previews").exists()
    config = settings(game)
    assert config["skin"] == "EDOPal-abyss"
    font_path, size = config["textfont"].rsplit(" ", 1)
    assert (game / font_path).read_bytes() == (cached / "fonts/text.ttf").read_bytes()
    assert size == "12"
    assert (game / config["numfont"]).read_bytes() == (cached / "fonts/num.ttf").read_bytes()

    restored = theme.undo()
    assert "config/system.conf" in restored.restored and not restored.conflicts
    assert conf.read_bytes() == before
    assert not (skin / "skin.xml").exists()
    assert ThemeService(core).state().active_theme is None


def test_custom_slot_survives_switches_and_reopening(core, packs):
    game = core.installs.current().root
    theme = ThemeService(core)
    state = theme.apply_cardback("opponent", packs[ANIME])
    chosen = (core.packs.cached(ANIME) / "back.png").read_bytes()
    assert state.cardbacks == {"you": VORTEX, "opponent": ANIME}
    assert (game / "textures/cover2.png").read_bytes() == chosen
    assert not (game / "textures/cover.png").exists()

    reopened = ThemeService(core)
    assert reopened.state().cardbacks["opponent"] == ANIME
    for slug in ("abyss", "moon"):
        state = reopened.apply_theme(packs[f"theme-{slug}"])
        assert state.cardbacks == {"you": VORTEX, "opponent": ANIME}
        managed = game / f"skin/EDOPal-{slug}/textures"
        assert (managed / "cover2.png").read_bytes() == chosen
        assert (managed / "cover.png").read_bytes() == (core.packs.cached(VORTEX) / "back.png").read_bytes()

    active_files = game / "skin/EDOPal-moon/textures"
    reopened.apply_cardback("you", packs[OCG])
    assert (active_files / "cover.png").read_bytes() == (core.packs.cached(OCG) / "back.png").read_bytes()
    assert (active_files / "cover2.png").read_bytes() == chosen
    assert not (game / "textures/cover.png").exists()
    reopened.undo()
    assert (active_files / "cover.png").read_bytes() == (core.packs.cached(VORTEX) / "back.png").read_bytes()
    assert reopened.state().cardbacks == {"you": VORTEX, "opponent": ANIME}


@pytest.mark.parametrize("original_skin", ["My original skin", None])
def test_restore_the_original_skin_and_fonts_after_multiple_theme_changes(core, packs, original_skin):
    game = core.installs.current().root
    conf = game / "config/system.conf"
    original = "sound_volume = 37\ntextfont = fonts/Personal.ttf 15\n"
    if original_skin:
        original += f"skin = {original_skin}\n"
    conf.write_text(original)
    theme = ThemeService(core)
    theme.apply_theme(packs["theme-abyss"])
    theme.apply_theme(packs["theme-nebula"])
    conf.write_text(conf.read_text().replace("sound_volume = 37", "sound_volume = 74"))
    restored = ThemeService(core).apply_theme(None)
    assert restored.active_theme is None
    config = settings(game)
    assert config.get("skin") == original_skin
    assert config["textfont"] == "fonts/Personal.ttf 15"
    assert "numfont" not in config
    assert config["sound_volume"] == "74"


def test_customize_accent_and_both_fonts_then_undo(core, packs):
    game = core.installs.current().root
    original = settings(game)
    theme = ThemeService(core)
    theme.apply_theme(packs["theme-abyss"])
    themed_fonts = settings(game)
    xml = game / "skin/EDOPal-abyss/skin.xml"
    initial = xml.read_bytes()
    state = theme.customize("#e7b052", False)
    assert state.active_theme == "theme-abyss" and not state.theme_font
    assert state.accent.lower() == "#e7b052"
    colors = ET.parse(xml)
    color = colors.find("Global/EGDC_ICON_HIGH_LIGHT")
    assert tuple(int(color.get(channel)) for channel in ("r", "g", "b")) == (231, 176, 82)
    assert color.get("a") == "255"
    background = colors.find("Global/EGDC_WINDOW")
    assert tuple(background.get(channel) for channel in ("r", "g", "b")) == ("20", "27", "36")
    assert settings(game)["textfont"] == original["textfont"]
    assert settings(game).get("numfont") == original.get("numfont")
    theme.undo()
    assert xml.read_bytes() == initial
    assert theme.state().theme_font
    assert settings(game)["textfont"] == themed_fonts["textfont"]
    assert settings(game)["numfont"] == themed_fonts["numfont"]


def test_failed_dependent_cardback_download_leaves_game_untouched(core, packs, monkeypatch):
    game = core.installs.current().root
    conf = game / "config/system.conf"
    before = conf.read_bytes()
    ensure = core.packs.ensure

    def download(pack, *args, **kwargs):
        if pack.id == OCG:
            raise OSError("Fixture connection interrupted")
        return ensure(pack, *args, **kwargs)

    monkeypatch.setattr(core.packs, "ensure", download)
    theme = ThemeService(core)
    with pytest.raises(OSError, match="Fixture connection interrupted"):
        theme.apply_theme(packs["theme-abyss"])
    assert conf.read_bytes() == before
    assert not (game / "skin/EDOPal-abyss/skin.xml").exists()
    assert not theme.state().can_undo
    monkeypatch.setattr(core.packs, "ensure", ensure)
    assert theme.apply_theme(packs["theme-abyss"]).active_theme == "theme-abyss"


def test_preferences_are_independent_between_game_folders(core, packs, tmp_path):
    theme = ThemeService(core)
    first = core.installs.current().root
    theme.apply_cardback("you", packs[ANIME])
    other = tmp_path / "second-game"
    other.mkdir()
    core.installs.select(other)
    assert theme.state().cardbacks["you"] == VORTEX
    assert not theme.state().can_undo
    core.installs.select(first)
    assert theme.state().cardbacks["you"] == ANIME and theme.state().can_undo


def test_optional_manifest_sleeve_defaults_fall_back_to_feature_defaults(core, packs):
    pack = packs["theme-nebula"]
    cached = core.packs.ensure(pack)
    manifest_path = cached / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    del manifest["default_cardbacks"]
    manifest_path.write_text(json.dumps(manifest))
    state = ThemeService(core).apply_theme(pack)
    assert state.back_images["you"].read_bytes() == (core.packs.cached(VORTEX) / "back.png").read_bytes()
    assert state.back_images["opponent"].read_bytes() == (core.packs.cached(OCG) / "back.png").read_bytes()


def test_cancel_during_download_preserves_files_and_receipt(core, packs):
    theme = ThemeService(core)
    original = (core.installs.current().root / "config/system.conf").read_bytes()
    stop = Event()
    context = JobContext(lambda done, total, message: stop.set(), stop)
    with pytest.raises(CancelledError):
        theme.apply_theme(packs["theme-abyss"], context)
    assert not theme.state().can_undo
    assert (core.installs.current().root / "config/system.conf").read_bytes() == original


def test_real_storage_preserves_player_edit_on_undo(core, packs):
    core.storage = SafeStorage(core.paths, core.installs.run_state)
    theme = ThemeService(core)
    game = core.installs.current().root
    conf = game / "config/system.conf"
    before = b"\xef\xbb\xbfskin  =  Original\r\n; My game configuration\r\nsound_volume = 13\r\n"
    conf.write_bytes(before)
    theme.apply_theme(packs["theme-abyss"])
    assert b"; My game configuration\r\nsound_volume = 13\r\n" in conf.read_bytes()
    edited = "skin/EDOPal-abyss/skin.xml"
    player_edit = b'<Skin><Properties><Name data="My edit"/></Properties></Skin>'
    (game / edited).write_bytes(player_edit)
    result = theme.undo()
    assert edited in result.conflicts
    assert (game / edited).read_bytes() == player_edit
    assert conf.read_bytes() == before


def test_personal_sleeves_follow_game_search_order_and_external_edits(core, packs):
    theme = ThemeService(core)
    game = core.installs.current().root
    base_dir = game / "textures"
    base_dir.mkdir(exist_ok=True)
    player_jpg = base_dir / "cover.jpg"
    Image.new("RGB", (4, 6), "#ac073a").save(player_jpg)
    initial = theme.state()
    assert initial.back_images == {"you": player_jpg, "opponent": player_jpg}
    assert initial.unknown_back_slots == {"you", "opponent"}

    chosen = theme.apply_cardback("you", packs[ANIME])
    chosen_png = base_dir / "cover.png"
    assert chosen.back_images["you"] == chosen_png
    assert "you" not in chosen.unknown_back_slots
    original_skin = game / "skin" / settings(game)["skin"] / "textures"
    original_skin.mkdir(parents=True, exist_ok=True)
    skin_jpg = original_skin / "cover.jpg"
    skin_jpg.write_bytes(player_jpg.read_bytes())
    assert theme.state().back_images["you"] == skin_jpg
    assert "you" in theme.state().unknown_back_slots
    skin_jpg.unlink()
    assert "you" not in theme.state().unknown_back_slots
    Image.new("RGB", (4, 6), "#063a51").save(chosen_png)
    edited = theme.state()
    assert edited.back_images["you"] == chosen_png
    assert "you" in edited.unknown_back_slots


def test_customize_preserves_skin_edits_made_while_awaiting_storage(core, packs, monkeypatch):
    theme = ThemeService(core)
    theme.apply_theme(packs["theme-abyss"])
    xml = core.installs.current().root / "skin/EDOPal-abyss/skin.xml"
    edited = xml.read_bytes().replace(b'data="Abyss"', b'data="My skin"')
    changeset = core.storage.changeset

    @contextmanager
    def simultaneous_edit(*args, **kwargs):
        xml.write_bytes(edited)
        with changeset(*args, **kwargs) as cs:
            yield cs

    monkeypatch.setattr(core.storage, "changeset", simultaneous_edit)
    with pytest.raises(ValueError):
        theme.customize("#e7b052", False)
    assert xml.read_bytes() == edited
    assert theme.state().theme_font

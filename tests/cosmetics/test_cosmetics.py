import pytest
from PySide6.QtCore import QRectF, Qt

from edopal.core.jobs import JobContext
from edopal.features.cosmetics.duel import MARKERS, fit
from edopal.features.cosmetics.icons import KEPT, SLOTS, apply_icons
from edopal.features.themes.service import ThemeService


@pytest.mark.parametrize("slot", ["you", "opponent"])
def test_museum_focus_credits_equip_replace_and_undo(core, page, qtbot, slot):
    museum = page.museum
    assert museum.buttons["you"].size() == museum.buttons["opponent"].size()
    assert len(museum.tiles) == 4
    for group in {p.meta["group"] for p in museum.packs.values()}:
        eras = [t.pack.meta["era"] for t in museum.tiles.values() if t.pack.meta["group"] == group]
        assert eras == sorted(eras)
    for key in ("cardback-zexal-anime", "cardback-ocg-1998"):
        qtbot.mouseClick(museum.tiles[key], Qt.MouseButton.LeftButton)
        assert museum.selected == key
        assert museum.packs[key].meta["label"] in museum.source.text()
        assert museum.credits.toPlainText() and not museum.art.pix.isNull()
        assert museum.buttons[slot].isEnabled()
        qtbot.mouseClick(museum.buttons[slot], Qt.MouseButton.LeftButton)
        assert ThemeService(core).state().cardbacks[slot] == key
        assert museum.slots[slot].title.text() == museum.packs[key].name
    page.undo.click()
    assert ThemeService(core).state().cardbacks[slot] == "cardback-zexal-anime"


def test_unrecognized_current_cover_keeps_its_art_and_label(core, page):
    game = core.installs.current().root
    source = core.packs.root / "cardbacks/zexal-anime/back.png"
    (game / "textures/cover.png").write_bytes(source.read_bytes())
    page._refresh()
    slot = page.museum.slots["you"]
    assert slot.title.text() == "Current Game Back"
    assert slot.portrait.height() >= 70
    slot.click()
    assert page.museum.name.text() == "Current Game Back"
    assert not page.museum.art.pix.isNull()


def test_all_rows_change_the_corresponding_field_pixels_before_apply(page, core):
    page.tab_buttons["icons"].click()
    editor = page.editor
    preview = editor.preview
    frame = fit(QRectF(preview.rect()), preview.field)
    for key in SLOTS:
        choice = "minimal" if key in KEPT else "A"
        before = preview.grab().toImage()
        editor.buttons[key, choice].click()
        after = preview.grab().toImage()
        x, y, size = {"settings": (590, 24, 24), "lpf": (100, 32, 18)}.get(key, MARKERS.get(key, (0, 0, 0)))
        scale = frame.width() / 620
        region = QRectF(
            frame.x() + (x - size / 2) * scale, frame.y() + (y - size / 2) * scale, size * scale, size * scale
        ).toRect()
        assert before.copy(region) != after.copy(region), key
        assert editor.current[key].text().lower() == choice.lower()
    assert list((core.installs.current().root / "textures").glob("*.png")) == []


@pytest.mark.parametrize("skin", ["", "EDOPal-moon"])
def test_apply_exact_pngs_and_undo_originals(core, page, skin):
    root = core.installs.current().root
    if skin:
        (root / "skin" / skin / "textures").mkdir(parents=True)
        (root / "config/system.conf").write_text(f"skin = {skin}\n")
    dest = (root / "skin" / skin if skin else root) / "textures"
    original = (core.packs.root / "icons/stock/textures/act.png").read_bytes()
    (dest / "act.png").write_bytes(original)
    page.open_tab("icons")
    editor = page.editor
    editor.presets["minimal-glass"].click()
    editor.buttons["number", "D"].click()
    editor.buttons["lpf", "C"].click()
    editor.apply.click()
    assert page.undo.isEnabled()
    for key, choice in editor.choices.items():
        assert (dest / f"{key}.png").read_bytes() == editor.library.textures[key, choice].read_bytes()
    assert editor.choices["number"] == "D"
    assert not editor.apply.isEnabled()
    page.undo.click()
    assert (dest / "act.png").read_bytes() == original
    assert not (dest / "number.png").exists()
    assert set(editor.choices.values()) == {"stock"}


def test_reset_to_preset_and_reject_stale_skin(core, page):
    page.open_tab("icons")
    editor = page.editor
    editor.presets["minimal-gold"].click()
    editor.buttons["attack", "E"].click()
    editor.reset.click()
    assert set(editor.choices.values()) == {"minimal-gold"}
    install = core.installs.current()
    with pytest.raises(ValueError, match="skin changed"):
        apply_icons(core, install, editor.library, editor.choices, "skin/EDOPal-moon/textures", JobContext())
    assert not (install.root / "textures/attack.png").exists()

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

from edopal.features.themes import register
from edopal.features.themes.service import ThemeService


@pytest.fixture
def page(core, qtbot):
    widget = register(core).page(core)
    qtbot.addWidget(widget)
    widget.resize(1280, 800)
    widget.show()
    return widget


def test_browse_preview_select_then_apply_and_undo(core, page, qtbot):
    spec = register(core)
    assert (spec.id, spec.label, spec.icon, spec.order) == ("themes", "Themes", "themes", 20)
    assert list(page.tiles)[0] == "default"
    qtbot.mouseClick(page.tiles["theme-abyss"], Qt.MouseButton.LeftButton)
    qtbot.mouseMove(page.tiles["theme-moon"])
    qtbot.waitUntil(lambda: page.hovered == "theme-moon")
    assert page.selected == "theme-abyss"
    qtbot.mouseClick(page.modes["deck"], Qt.MouseButton.LeftButton)
    assert page.scene == "deck"
    assert all(core.packs.cached(pack.id) is None for pack in core.packs.catalog())

    qtbot.mouseClick(page.apply_button, Qt.MouseButton.LeftButton)
    assert ThemeService(core).state().active_theme == "theme-abyss"
    assert page.undo_button.isEnabled()
    qtbot.mouseClick(page.undo_button, Qt.MouseButton.LeftButton)
    assert ThemeService(core).state().active_theme is None


@pytest.mark.parametrize("slot", ["you", "opponent"])
def test_inline_cardback_choice_applies_in_one_click(core, page, qtbot, slot):
    qtbot.mouseClick(page.slots[slot], Qt.MouseButton.LeftButton)
    picker = page.drawer
    assert picker and page.isAncestorOf(picker) and picker.isVisible()
    labels = {label.text() for label in picker.findChildren(QLabel)}
    assert {"TCG / OCG", "ANIME", "Yu-Gi-Oh! ZEXAL anime (2011)"} <= labels
    choice = picker.findChild(QPushButton, "pick-cardback-zexal-anime")
    assert choice and choice.isVisible()
    qtbot.mouseClick(choice, Qt.MouseButton.LeftButton)
    assert ThemeService(core).state().cardbacks[slot] == "cardback-zexal-anime"
    assert page.slots["you"].isVisible() and page.slots["opponent"].isVisible()


def test_changing_the_same_slot_twice_refreshes_its_portrait(core, page, qtbot):
    slot = page.slots["you"]

    def choose(pack_id):
        qtbot.mouseClick(slot, Qt.MouseButton.LeftButton)
        choice = page.drawer.findChild(QPushButton, f"pick-{pack_id}")
        qtbot.mouseClick(choice, Qt.MouseButton.LeftButton)
        assert ThemeService(core).state().cardbacks["you"] == pack_id
        return slot.portrait.grab().toImage()

    first = choose("cardback-zexal-anime")
    second = choose("cardback-ocg-1998")
    assert first != second


def test_customize_active_theme_from_inline_drawer(core, page, qtbot):
    qtbot.mouseClick(page.tiles["theme-abyss"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.apply_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.customize_button, Qt.MouseButton.LeftButton)
    drawer = page.drawer
    assert drawer.isVisible() and page.isAncestorOf(drawer)
    buttons = drawer.findChildren(QPushButton)
    gold = next(button for button in buttons if button.accessibleName() == "Gold")
    qtbot.mouseClick(gold, Qt.MouseButton.LeftButton)
    font = drawer.findChild(QCheckBox)
    assert font.isChecked()
    qtbot.mouseClick(font, Qt.MouseButton.LeftButton)
    save = next(button for button in buttons if button.text() == "Save changes")
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    state = ThemeService(core).state()
    assert state.accent == f"#{gold.objectName().removeprefix('swatch-')}"
    assert not state.theme_font

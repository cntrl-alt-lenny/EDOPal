from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

from edopal.features.themes import register
from edopal.features.themes.gallery import PreviewCanvas
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
    assert "theme-abyss" in page.state.installed_themes
    assert page.undo_button.isEnabled()
    qtbot.mouseClick(page.undo_button, Qt.MouseButton.LeftButton)
    assert ThemeService(core).state().active_theme is None


@pytest.mark.parametrize("modifiers", [Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier])
def test_horizontal_library_overflow_wheel_and_arrow_navigation(page, qtbot, modifiers):
    base = page.packs["theme-abyss"]
    for n in range(15):
        pack = replace(base, id=f"theme-study-{n}", name=f"Study {n}")
        page.packs[pack.id] = pack
    page._build_tiles()
    QApplication.processEvents()
    bar = page.shelf.horizontalScrollBar()
    assert bar.isVisible() and bar.maximum() > 0
    pos = QPointF(30, 20)
    wheel = QWheelEvent(
        pos, pos, QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False
    )
    QApplication.sendEvent(page.shelf.viewport(), wheel)
    assert bar.value() > 0
    keys = list(page.tiles)
    page.tiles[keys[-2]].setFocus()
    qtbot.keyClick(page.tiles[keys[-2]], Qt.Key.Key_Right)
    assert page.selected == keys[-1] and page.tiles[keys[-1]].hasFocus()
    assert bar.value() == bar.maximum()
    qtbot.keyClick(page.tiles[keys[-1]], Qt.Key.Key_Left)
    assert page.selected == keys[-2]
    assert page.shelf.geometry().bottom() < page.canvas.geometry().top()
    assert abs(page.canvas._frame().width() / page.canvas._frame().height() - 1.6) < 0.001


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
    save = next(button for button in buttons if button.text() == "Save Changes")
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    state = ThemeService(core).state()
    assert state.accent == f"#{gold.objectName().removeprefix('swatch-')}"
    assert not state.theme_font


def test_preview_keeps_capture_edges_and_fills_both_margins(qtbot):
    preview = PreviewCanvas()
    qtbot.addWidget(preview)
    preview.resize(720, 300)
    capture = QPixmap(480, 300)
    left, right = QColor("#299eb1"), QColor("#d49629")
    capture.fill(left)
    with QPainter(capture) as painter:
        painter.fillRect(240, 0, 240, 300, right)
    preview.set_preview(capture, animate=False)
    image = preview.grab().toImage()
    frame = preview._frame()
    left_edge, right_edge = round(frame.left()), round(frame.right())
    assert image.pixelColor(left_edge + 2, 150) == left
    assert image.pixelColor(right_edge - 2, 150) == right
    for margin, original in ((45, left), (675, right)):
        tinted = image.pixelColor(margin, 150)
        assert 0.58 < tinted.value() / original.value() < 0.68
        assert abs(tinted.hue() - original.hue()) <= 2

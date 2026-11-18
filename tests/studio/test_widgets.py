from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from studio import theme as t
from studio.shell import StudioWindow
from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import Hero, ImageTileGrid, SearchField, SwatchButton, Tile
from studio.widgets import tiles as tiles_module


def test_gallery_activates_once_without_native_return_binding(qtbot: QtBot) -> None:
    class EditOnReturnGrid(ImageTileGrid):
        def keyPressEvent(self, event: QKeyEvent) -> None:
            # macOS offers Return to an editor instead of activating a read-only row.
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                event.ignore()
            else:
                super().keyPressEvent(event)

    runner = TaskRunner()
    grid = EditOnReturnGrid(runner)
    qtbot.addWidget(grid)
    grid.set_tiles([Tile("First", Path("first")), Tile("Second", Path("second"))])
    grid.setCurrentIndex(grid.model().index(1, 0))
    rows = []
    grid.activated.connect(lambda index: rows.append(index.row()))
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        qtbot.keyClick(grid, key)
    assert rows == [1, 1]


def test_large_grid_decodes_visible_items_and_reflows(
    qapp: QApplication, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    runner = TaskRunner()
    grid = ImageTileGrid(runner)
    qtbot.addWidget(grid)
    seen = []

    def read(path: Path, ctx: WorkerContext) -> QImage:
        seen.append(path)
        image = QImage(12, 12, QImage.Format.Format_RGB32)
        image.fill(QColor(t.GOLD))
        return image

    monkeypatch.setattr(tiles_module, "_read_thumbnail", read)
    items = [Tile(f"Sleeve {n}", tmp_path / f"{n}.png") for n in range(700)]
    grid.set_tiles(items)
    grid.resize(750, 440)
    grid.show()
    qtbot.waitUntil(lambda: len(grid.tiles.cache) >= 8)
    assert len(seen) < 40
    columns = max(1, (grid.viewport().width() - t.HAIR) // t.TILE_SIZE[0])
    assert grid.visualRect(grid.model().index(columns - 1)).top() == 0
    assert grid.visualRect(grid.model().index(0)).height() == grid.gridSize().height()
    grid.scrollToBottom()
    qtbot.waitUntil(lambda: items[-1].path in grid.tiles.cache)
    assert len(seen) < 70
    grid.resize(420, 440)
    qapp.processEvents()
    assert grid.gridSize().width() <= grid.viewport().width() // 2
    grid.set_tiles([items[0]])
    qtbot.waitUntil(lambda: items[0].path in grid.tiles.cache)
    assert grid.model().rowCount() == 1
    qtbot.waitUntil(lambda: not runner.jobs)


def test_search_and_keyboard_swatch(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    search = SearchField("Find sleeves")
    swatch = SwatchButton("Accent", QColor(t.GOLD))
    qtbot.addWidget(search)
    qtbot.addWidget(swatch)
    assert search.accessibleName() == "Find sleeves"
    qtbot.keyClicks(search, "Vortex")
    assert search.text() == "Vortex"
    monkeypatch.setattr("studio.widgets.controls.QColorDialog.getColor", lambda *_: QColor(t.GREEN))
    with qtbot.waitSignal(swatch.colour_changed, timeout=2000) as changed:
        qtbot.keyClick(swatch, Qt.Key.Key_Return)
    assert changed.args[0] == QColor(t.GREEN)


def test_keyboard_scope_toggle_and_settings_return(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    card = window.current
    left, right = card.options.scope.buttons
    right.setFocus()
    qtbot.keyClick(right, Qt.Key.Key_Left)
    assert left.isChecked()
    qtbot.keyClick(card.options.settings, Qt.Key.Key_Return)
    switch = card.settings.toggles["field_art"]
    before = switch.isChecked()
    qtbot.keyClick(switch, Qt.Key.Key_Space)
    assert switch.isChecked() != before
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert card.state == "options"


def test_compact_hero_restores_page_sizes_and_respects_hidden_subviews(
    make_window: Callable[..., StudioWindow], qtbot: QtBot
) -> None:
    window = make_window()
    window.resize(1600, 1000)
    window.navigate("music")
    qtbot.waitUntil(lambda: window.current.state == "home")
    hero = window.current.hero
    qtbot.waitUntil(hero.isVisible)
    normal = hero.minimumHeight(), hero.maximumHeight()
    window.resize(1280, 800)
    qtbot.waitUntil(
        lambda: (
            hero.height() < normal[0] and hero.headline.isVisible() and hero.description.isVisible()
        )
    )
    new = Hero("Section", "New view", "Created after the window was resized.")
    new.setParent(window.current)
    new.setFixedHeight(240)
    new.show()
    assert new.maximumHeight() < 240
    new.setFixedHeight(260)
    new.hide()
    window.resize(1600, 1000)
    assert new.isHidden() and new.minimumHeight() == new.maximumHeight() == 260
    assert (hero.minimumHeight(), hero.maximumHeight()) == normal

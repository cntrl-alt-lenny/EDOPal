from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from studio import theme as t
from studio.shell import StudioWindow
from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import ImageTileGrid, SearchField, SwatchButton, Tile
from studio.widgets import tiles as tiles_module


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

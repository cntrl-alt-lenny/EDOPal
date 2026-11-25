import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPixmap

from edopal.ui import theme
from edopal.ui.widgets import Hero, Notice, Panel, PrimaryButton, ProgressBlock, QuietButton, Tile, TileGrid, TileRow


@pytest.mark.parametrize(
    "name", ("hdcards", "themes", "music", "formats", "settings", "launch", "folder", "check", "warning", "undo")
)
def test_icon_renders_in_requested_ink_at_double_density(qapp, name):
    icon = theme.icon(name, theme.GOLD)
    image = icon.pixmap(QSize(24, 24), 2).toImage()
    assert image.size() == QSize(48, 48)
    assert any(image.pixelColor(x, y) == QColor(theme.GOLD) for x in range(48) for y in range(48))


def test_finite_grids_preserve_order_and_select_exclusively(qtbot):
    tiles = [Tile(str(i), "Album") for i in range(5)]
    grid = TileGrid(tiles, columns=3)
    qtbot.addWidget(grid)
    grid.show()
    assert grid.grid.getItemPosition(3) == (1, 0, 1, 1)
    with qtbot.waitSignal(tiles[1].previewed):
        grid.activateWindow()
        tiles[1].setFocus()
    qtbot.keyClick(tiles[1], Qt.Key.Key_Space)
    assert grid.group.checkedId() == 1
    with qtbot.waitSignal(tiles[4].previewed):
        qtbot.keyClick(tiles[1], Qt.Key.Key_Down)
    assert tiles[4].hasFocus()
    assert grid.group.checkedId() == 1
    qtbot.keyClick(tiles[4], Qt.Key.Key_Return)
    assert grid.group.checkedId() == 4
    assert not tiles[1].isChecked()
    assert tiles[4].accessibleName() == "4"
    assert tiles[4].accessibleDescription() == "Album"
    grid.grab()

    row = TileRow([Tile("One"), Tile("Two")])
    qtbot.addWidget(row)
    row.add_tile(Tile("Three"))
    assert row.grid.getItemPosition(2) == (0, 2, 1, 1)


def test_progress_handles_large_downloads_and_indeterminate_totals(qtbot):
    progress = ProgressBlock("Downloading")
    qtbot.addWidget(progress)
    assert progress.bar.maximum() == 0
    progress.set_progress(2**32, 2**33, "Music pack")
    assert progress.percent.text() == "50%"
    assert progress.bar.value() == 500
    assert progress.label.text() == progress.bar.accessibleName() == "Music pack"
    progress.set_progress(3, 0)
    assert progress.bar.maximum() == 0
    assert not progress.percent.text()


def test_blocks_render_art_and_message_states(qtbot, qapp):
    theme.apply(qapp)
    panel = Panel()
    qtbot.addWidget(panel)
    hero = Hero("Music", "Albums", "Choose an album")
    panel.body.addWidget(hero)
    art = QPixmap(64, 32)
    art.fill(QColor("blue"))
    hero.set_art(art)
    panel.body.addWidget(PrimaryButton("Launch", icon="launch"))
    panel.body.addWidget(QuietButton("Folder", icon="folder"))
    notice = Notice()
    panel.body.addWidget(notice)
    assert notice.isHidden()
    notice.set_message("Ready", "success")
    panel.resize(750, 400)
    panel.show()
    qtbot.waitUntil(notice.isVisible)
    shot = hero.grab().toImage()
    assert shot.pixelColor(shot.width() - 10, shot.height() // 2).blue() > 230
    assert notice.property("kind") == "success"
    notice.set_message("")
    assert notice.isHidden()

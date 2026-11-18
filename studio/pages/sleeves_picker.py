"""Inline pattern and palette choices beside the full-size sleeve preview."""

from pathlib import Path

from PySide6.QtCore import QModelIndex, QSignalBlocker, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QVBoxLayout, QWidget

from sleeves import PALETTES, PATTERNS, Recipe, render
from sleeves.catalog import PALETTE_FAMILIES
from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import Tile, label, margins

from .skins_grid import AppearanceGrid
from .sleeves_widgets import Menu


class VisualMenu(Menu):
    requested = Signal()

    def showPopup(self) -> None:
        self.requested.emit()


class PrintPicker(QWidget):
    chosen = Signal(str)

    def __init__(self, tasks: TaskRunner) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        margins(layout, 0)
        self.heading = label("", "subheading")
        layout.addWidget(self.heading)
        self.group = Menu("Filter picker")
        layout.addWidget(self.group)
        self.grid = AppearanceGrid(tasks, self.thumbnail)
        layout.addWidget(self.grid, 1)
        self.group.currentIndexChanged.connect(self.populate)
        self.grid.clicked.connect(self.choose)
        self.grid.activated.connect(self.choose)
        self.patterns = True
        self.pattern = ""
        self.palette = ""
        self.selected = ""

    @staticmethod
    def thumbnail(path: Path, worker: WorkerContext) -> QImage:
        if worker.cancel.is_set():
            return QImage()
        pattern, palette = path.parts
        return render(Recipe(pattern, PALETTES[palette]), thumbnail=True)

    def present(self, patterns: bool, pattern: str, palette: str) -> None:
        self.patterns, self.pattern, self.palette = patterns, pattern, palette
        self.selected = pattern if patterns else palette
        self.heading.setText("Choose a pattern" if patterns else "Choose a palette")
        self.grid.setAccessibleName(self.heading.text())
        groups = (
            {p.family for p in PATTERNS.values()} if patterns else set(PALETTE_FAMILIES.values())
        )
        with QSignalBlocker(self.group):
            self.group.clear()
            self.group.addItems(
                ["All families" if patterns else "All colors", *sorted(groups - {""})]
            )
        self.populate()
        self.show()
        self.grid.setFocus()

    def populate(self) -> None:
        group = self.group.currentText() if self.group.currentIndex() > 0 else ""
        if self.patterns:
            rows = [
                (key, spec.name, Path(key) / (self.palette or spec.palette))
                for key, spec in PATTERNS.items()
                if not group or group == spec.family
            ]
        else:
            rows = [
                (name, name, Path(self.pattern) / name)
                for name in PALETTES
                if not group or group == PALETTE_FAMILIES.get(name)
            ]
        self.keys = [key for key, _, _ in rows]
        self.grid.set_tiles([Tile(name, path, tooltip=name) for _, name, path in rows])
        if self.selected in self.keys:
            index = self.grid.model().index(self.keys.index(self.selected), 0)
            self.grid.setCurrentIndex(index)
            self.grid.scrollTo(index)
        else:
            self.grid.scrollToTop()

    def choose(self, index: QModelIndex) -> None:
        if self.isVisible() and index.isValid():
            self.chosen.emit(self.keys[index.row()])

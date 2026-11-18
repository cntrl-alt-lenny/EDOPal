"""Appearance galleries filter rows independently from their rendered artwork."""

from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtGui import QImage, QPixmap

from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import ImageTileGrid, Tile


class ThumbnailCache(OrderedDict[Path, QPixmap]):
    budget = 24 * 1024**2

    def __setitem__(self, key: Path, pixmap: QPixmap) -> None:
        super().__setitem__(key, pixmap)
        total = sum(p.width() * p.height() * max(4, p.depth() // 8) for p in self.values())
        while total > self.budget:
            _, old = self.popitem(last=False)
            total -= old.width() * old.height() * max(4, old.depth() // 8)


class AppearanceGrid(ImageTileGrid):
    def __init__(
        self, runner: TaskRunner, loader: Callable[[Path, WorkerContext], QImage] | None = None
    ) -> None:
        if loader is None:
            super().__init__(runner)
        else:
            super().__init__(runner, loader)
        self.tiles.cache = ThumbnailCache()

    def invalidate(self) -> None:
        # Old worker completions can finish, but cannot publish into the new collection.
        self.tiles.generation += 1
        self.tiles.cache.clear()

    def suspend(self) -> None:
        self.tiles.generation += 1
        for job in self.tiles.pending:
            job.cancel()

    def set_tiles(self, entries: Sequence[Tile]) -> None:
        model = self.tiles
        model.beginResetModel()
        model.items = tuple(entries)
        model.endResetModel()

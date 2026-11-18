"""Virtualized gallery with bounded, asynchronous thumbnail decoding."""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, Slot
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from studio import theme as t
from studio.icons import icon
from studio.tasks import Job, TaskRunner, WorkerContext


@dataclass(frozen=True)
class Tile:
    title: str
    path: Path


class _Tiles(QAbstractListModel):
    def __init__(self, view: QListView, runner: TaskRunner) -> None:
        super().__init__(view)
        self.runner = runner
        self.view = view
        self.items: tuple[Tile, ...] = ()
        self.cache: OrderedDict[Path, QPixmap] = OrderedDict()
        self.generation = 0
        self.pending: dict[Job, tuple[int, Path]] = {}

    def replace(self, items: Sequence[Tile]) -> None:
        self.beginResetModel()
        self.generation += 1
        self.items = tuple(items)
        self.cache.clear()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return item.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(item.path)
        if role != Qt.ItemDataRole.DecorationRole:
            return None
        path = item.path
        if path in self.cache:
            self.cache.move_to_end(path)
            return self.cache[path]
        key = (self.generation, path)
        if key not in self.pending.values() and len(self.pending) < 4:
            job = self.runner.start(lambda ctx: _read_thumbnail(path, ctx))
            self.pending[job] = key
            job.result.connect(self._loaded)
            job.error.connect(self._failed)
        return None

    @Slot(object)
    def _loaded(self, image: QImage) -> None:
        generation, path = self.pending.pop(self.sender())
        if generation == self.generation:
            self.cache[path] = QPixmap.fromImage(image)
            if len(self.cache) > 512:
                self.cache.popitem(last=False)
        self.view.viewport().update()

    @Slot(object)
    def _failed(self, error: BaseException) -> None:
        self._loaded(QImage())


def _read_thumbnail(path: Path, ctx: WorkerContext) -> QImage:
    if ctx.cancel.is_set():
        return QImage()
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid():
        reader.setScaledSize(
            size.scaled(QSize(*t.THUMB_SIZE) * 2, Qt.AspectRatioMode.KeepAspectRatio)
        )
    return reader.read()


class _TileDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return self.parent().gridSize()

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(t.XS, t.XS, -t.XS, -t.XS)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setBrush(QColor(t.ACTIVE if selected else t.HOVER if hover else t.PANEL))
        p.setPen(
            QPen(QColor(t.GOLD if selected or focused else t.EDGE), t.FOCUS if focused else t.HAIR)
        )
        p.drawRoundedRect(rect, t.R_SMALL, t.R_SMALL)
        inner = rect.adjusted(t.SM, t.SM, -t.SM, -t.XXL)
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if pix and not pix.isNull():
            size = pix.size().scaled(inner.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = inner.adjusted(0, 0, 0, 0)
            target.setSize(size)
            target.moveCenter(inner.center())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(target, pix)
        else:
            center = inner.center()
            side = t.ICON_BIG
            icon("cards", t.MUTED).paint(
                p, center.x() - side // 2, center.y() - side // 2, side, side
            )
        p.setPen(QColor(t.INK))
        title = p.fontMetrics().elidedText(index.data(), Qt.TextElideMode.ElideRight, inner.width())
        p.drawText(
            rect.adjusted(t.SM, 0, -t.SM, -t.SM),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            title,
        )
        p.restore()


class ImageTileGrid(QListView):
    def __init__(self, runner: TaskRunner) -> None:
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setGridSize(QSize(*t.TILE_SIZE))
        self.tiles = _Tiles(self, runner)
        self.setModel(self.tiles)
        self.setItemDelegate(_TileDelegate(self))

    def set_tiles(self, entries: Sequence[Tile]) -> None:
        self.tiles.replace(entries)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # IconMode wraps when the next cell reaches the inclusive right edge.
        width = self.viewport().width() - t.HAIR
        columns = max(1, width // t.TILE_SIZE[0])
        self.setGridSize(QSize(max(1, width // columns), t.TILE_SIZE[1]))

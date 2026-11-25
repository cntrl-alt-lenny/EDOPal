"""Selectable, keyboard-focusable artwork tiles and finite catalog layouts."""

from collections.abc import Iterable

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QFocusEvent, QKeyEvent, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from edopal.ui import theme as t
from edopal.ui.widgets.art import ImageSource, draw_cover, pixmap
from edopal.ui.widgets.blocks import _Button


class _Cover(QWidget):
    def __init__(self, image: ImageSource | None, parent: QWidget):
        super().__init__(parent)
        self.image = pixmap(image)
        self.setFixedHeight(100)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event: QPaintEvent) -> None:
        box = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(box, t.R_SMALL, t.R_SMALL)
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            painter.setClipPath(clip)
            painter.fillPath(clip, QColor(t.RAISED))
            draw_cover(painter, self.image, box)
            if self.parentWidget().isChecked():
                badge = box.adjusted(box.width() - 30, 6, -6, 30 - box.height())
                painter.setBrush(QColor(t.GOLD))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(badge)
                t.icon("check", t.ON_GOLD).paint(painter, badge.adjusted(4, 4, -4, -4).toRect())


class _TileLabel(QLabel):
    def __init__(self, text: str, role: str):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("role", role)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event: QPaintEvent) -> None:
        with QPainter(self) as painter:
            text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
            painter.setPen(self.palette().windowText().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter, text)


class Tile(_Button):
    role = "tile"
    previewed = Signal()

    def __init__(self, title: str, caption: str = "", *, image: ImageSource | None = None, parent: QWidget | None = None):
        super().__init__("", parent)
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.setAccessibleDescription(caption)
        self.setToolTip("\n".join(filter(None, (title, caption))))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(t.SM, t.SM, t.SM, t.MD)
        self.body.setSpacing(t.XS)
        self.cover = _Cover(image, self)
        self.body.addWidget(self.cover)
        self.body.addSpacing(t.XS)
        self.body.addWidget(_TileLabel(title, "tileTitle"))
        if caption:
            self.body.addWidget(_TileLabel(caption, "muted"))
        self.toggled.connect(self.cover.update)

    def sizeHint(self) -> QSize:
        return QSize(t.TILE_SIZE[0], self.body.sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return self.body.minimumSize()

    def set_image(self, image: ImageSource | None) -> None:
        self.cover.image = pixmap(image)
        self.cover.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        if self.isEnabled():
            self.previewed.emit()

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self.previewed.emit()


class TileGrid(QWidget):
    def __init__(self, tiles: Iterable[Tile] = (), *, columns: int = 3, parent: QWidget | None = None):
        super().__init__(parent)
        if columns < 1:
            raise ValueError("columns must be positive")
        self.columns = columns
        self.group = QButtonGroup(self)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(t.MD)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)
        for tile in tiles:
            self.add_tile(tile)

    def add_tile(self, tile: Tile) -> None:
        index = self.grid.count()
        self.group.addButton(tile, index)
        row, column = divmod(index, self.columns)
        self.grid.addWidget(tile, row, column)
        tile.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Qt clicks an exclusive group's button on arrow navigation. Gallery navigation
        # previews the item and leaves applying it to an explicit Space or Enter.
        if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress and not event.modifiers():
            forward = -1 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else 1
            step = {
                Qt.Key.Key_Left: (0, -forward),
                Qt.Key.Key_Right: (0, forward),
                Qt.Key.Key_Up: (-1, 0),
                Qt.Key.Key_Down: (1, 0),
            }.get(event.key())
            if step and isinstance(watched, Tile):
                row, column = divmod(self.group.id(watched), self.columns)
                while True:
                    row += step[0]
                    column += step[1]
                    item = self.grid.itemAtPosition(row, column)
                    if not item:
                        break
                    candidate = item.widget()
                    if candidate.isEnabled() and candidate.isVisible():
                        candidate.setFocus(Qt.FocusReason.TabFocusReason)
                        break
                event.accept()
                return True
        return super().eventFilter(watched, event)


class TileRow(TileGrid):
    def __init__(self, tiles: Iterable[Tile] = (), parent: QWidget | None = None):
        super().__init__(tiles, columns=1, parent=parent)

    def add_tile(self, tile: Tile) -> None:
        self.columns = self.grid.count() + 1
        self.grid.setColumnStretch(self.columns - 1, 1)
        super().add_tile(tile)

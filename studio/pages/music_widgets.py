"""Music-specific album sleeves, record artwork and track lists."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.pages.music_model import Album, track_count
from studio.tasks import TaskRunner
from studio.widgets import Button, ElidedLabel, Hero, ImageTileGrid, Tile, glyph, label, margins

STYLE = f"""
QTableWidget, QComboBox {{ background:{t.PANEL}; color:{t.INK}; border:1px solid {t.EDGE}; border-radius:{t.R_SMALL}px; }}
QComboBox {{ padding:{t.XS}px {t.SM}px; }}
QHeaderView::section {{ background:{t.RAISED}; color:{t.MUTED}; border:none; padding:{t.SM}px; text-align:left; }}
QTableWidget::item {{ padding:0 {t.SM}px; }}
QTableWidget::item:selected {{ background:{t.ACTIVE}; color:{t.PALE}; }}
QTableWidget:focus, QSlider:focus, QComboBox:focus {{ border:{t.FOCUS}px solid {t.PALE}; }}
QSlider::groove:horizontal {{ background:{t.EDGE}; height:{t.XS}px; }}
QSlider::sub-page:horizontal {{ background:{t.GOLD}; }}
QSlider::handle:horizontal {{ background:{t.GOLD}; width:{t.MD}px; margin:-{t.XS}px 0; border-radius:{t.R_SMALL}px; }}
"""


def action(text: str, callback: Callable, role: str = "secondary", icon: str = "") -> Button:
    button = Button(text, role, icon)
    button.clicked.connect(lambda: callback())
    return button


def row(*widgets: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(t.SM)
    for widget in widgets:
        layout.addWidget(widget)
    return layout


def table(headers: Sequence[str], name: str) -> QTableWidget:
    view = QTableWidget(0, len(headers))
    view.setAccessibleName(name)
    view.setHorizontalHeaderLabels(headers)
    view.setColumnWidth(0, t.TILE_SIZE[0])
    view.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    view.horizontalHeader().setMinimumSectionSize(t.ICON_BIG)
    view.verticalHeader().hide()
    view.verticalHeader().setDefaultSectionSize(t.BUTTON_H)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.setTabKeyNavigation(False)
    view.setAlternatingRowColors(True)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setMinimumHeight(t.TILE_SIZE[1])
    return view


def cells(view: QTableWidget, values: Sequence[str]) -> int:
    i = view.rowCount()
    view.insertRow(i)
    for col, value in enumerate(values):
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        view.setItem(i, col, item)
    return i


class RecordHero(Hero):
    def _print(self, p: QPainter, x: float, y: float, height: float) -> None:
        height = min(height, self.width() * 0.20)
        p.save()
        p.translate(x, y)
        p.rotate(-14)
        radius = height * 0.77
        p.setPen(QPen(QColor(t.BRONZE), t.HAIR))
        p.setBrush(QColor(t.RAIL))
        p.drawEllipse(QPointF(radius * 0.43, 0), radius, radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(t.EDGE))
        for i in range(4, 19):
            r = radius * i / 20
            p.drawEllipse(QPointF(radius * 0.43, 0), r, r)
        p.setBrush(QColor(t.BRONZE))
        p.drawEllipse(QPointF(radius * 0.43, 0), radius * 0.31, radius * 0.31)
        p.setBrush(QColor(t.RAIL))
        p.drawEllipse(QPointF(radius * 0.43, 0), t.XS, t.XS)
        p.restore()
        self.jacket(p, x - radius * 0.64, y, height * 1.32)

    def jacket(self, p: QPainter, x: float, y: float, height: float) -> None:
        super()._print(p, x, y, height)


class SceneButton(Button):
    def __init__(self, title: str, symbol: str, status: str, detail: str) -> None:
        super().__init__("", "action")
        self.setAccessibleName(title)
        self.setAccessibleDescription(f"{status}. {detail}")
        self.setToolTip(f"{title}\n{status}\n{detail}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        layout = QVBoxLayout(self)
        margins(layout, t.MD)
        layout.setSpacing(t.XS)
        line = QHBoxLayout()
        line.addWidget(glyph(symbol))
        line.addStretch()
        line.addWidget(label(status, "muted"))
        layout.addLayout(line)
        layout.addWidget(label(title, "subheading", True))
        self.detail = ElidedLabel()
        self.detail.set_value(detail)
        layout.addWidget(self.detail)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def paint_sleeve(p: QPainter, rect: QRectF, album: Album, pix: QPixmap | None) -> None:
    p.save()
    p.setClipRect(rect)
    if pix and not pix.isNull():
        p.fillRect(rect, QColor(t.RAIL))
        target = QRectF(pix.rect())
        target.setSize(target.size().scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio))
        target.moveCenter(rect.center())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(target, pix, QRectF(pix.rect()))
    else:
        palette = list(t.FOIL.values())
        dark, light = palette[sum(map(ord, album.game.title)) % len(palette)]
        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0, QColor(light))
        bg.setColorAt(1, QColor(dark))
        p.fillRect(rect, bg)
        p.setPen(QPen(QColor(t.BRONZE), t.HAIR))
        r = rect.height() * 0.82
        p.setBrush(Qt.BrushStyle.NoBrush)
        for ring in (1, 0.92, 0.8, 0.77, 0.4):
            p.drawEllipse(rect.bottomRight(), r * ring, r * ring)
        text = rect.adjusted(t.MD, t.SM, -t.MD, -t.SM)
        words = album.game.title.split()
        initials = "".join(word[0] for word in words[:3])
        p.setFont(t.font(t.DISPLAY, serif=True))
        p.setPen(QColor(t.PALE))
        p.drawText(text, Qt.AlignmentFlag.AlignVCenter, initials)
        p.setFont(t.font(t.CAPTION, bold=True))
        edition = p.fontMetrics().elidedText(
            album.edition, Qt.TextElideMode.ElideRight, int(text.width())
        )
        p.drawText(text, Qt.AlignmentFlag.AlignBottom, edition)
    p.restore()


class AlbumDelegate(QStyledItemDelegate):
    def __init__(self, grid: AlbumGrid) -> None:
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return self.grid.gridSize()

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        album = self.grid.albums[index.row()]
        p.save()
        rect = QRectF(option.rect.adjusted(t.XS, t.XS, -t.XS, -t.XS))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = option.state & (
            QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
        )
        p.setPen(QPen(QColor(t.GOLD if selected else t.EDGE), t.FOCUS if selected else t.HAIR))
        p.setBrush(QColor(t.ACTIVE if selected else t.PANEL))
        p.drawRoundedRect(rect, t.R_SMALL, t.R_SMALL)
        sleeve = rect.adjusted(t.SM, t.SM, -t.SM, -t.HUGE - t.SM)
        pix = index.data(Qt.ItemDataRole.DecorationRole) if album.game.cover_page else None
        paint_sleeve(p, sleeve, album, pix)
        caption = rect.adjusted(t.SM, 0, -t.SM, -t.SM)
        caption.setTop(sleeve.bottom() + t.XS)
        p.setFont(t.font(t.BODY, bold=True))
        title = p.fontMetrics().elidedText(
            album.game.title, Qt.TextElideMode.ElideRight, int(caption.width())
        )
        p.setPen(QColor(t.INK))
        p.drawText(caption, Qt.AlignmentFlag.AlignTop, title)
        p.setFont(t.font(t.SMALL))
        p.setPen(QColor(t.GOLD if album.tracks else t.MUTED))
        details = track_count(len(album.tracks)) + "  ·  " + album.game.series
        p.drawText(
            caption,
            Qt.AlignmentFlag.AlignBottom,
            p.fontMetrics().elidedText(details, Qt.TextElideMode.ElideRight, int(caption.width())),
        )
        p.restore()


class AlbumGrid(ImageTileGrid):
    def __init__(self, runner: TaskRunner) -> None:
        super().__init__(runner)
        self.albums: list[Album] = []
        self.setAccessibleName("Games")
        self.setItemDelegate(AlbumDelegate(self))
        self.setMinimumHeight(t.TILE_SIZE[1] + t.XL)
        self.setStyleSheet(f"QListView {{background:{t.CANVAS};padding:0;border:none;}}")

    def populate(self, albums: list[Album]) -> None:
        self.albums = albums
        self.set_tiles(
            [Tile(f"{a.game.title} · {track_count(len(a.tracks))}", cover(a)) for a in albums]
        )
        if albums:
            self.setCurrentIndex(self.model().index(0, 0))

    def viewportEvent(self, event: QEvent) -> bool:
        from PySide6.QtWidgets import QToolTip

        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                album = self.albums[index.row()]
                QToolTip.showText(event.globalPos(), album.game.title + "\n" + album.edition, self)
                return True
        return super().viewportEvent(event)


def cover(album: Album) -> Path:
    return t.ASSETS / "music/grid" / (album.game.id + ".png")

"""The searchable look gallery and its virtualized landscape tiles."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.context import AppContext
from studio.widgets import (
    Button,
    Hero,
    ImageTileGrid,
    Panel,
    SearchField,
    Tile,
    glyph,
    label,
    margins,
)

from .skins_model import FILTERS, Look, filter_looks
from .skins_service import Collection, SkinStore


class Choices(QWidget):
    changed = Signal(str)

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        margins(row, 0)
        row.setSpacing(t.XS)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, Button] = {}
        for name in names:
            btn = Button(name, "quiet")
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"QPushButton:checked {{background:{t.ACTIVE}; color:{t.GOLD};"
                f"border-color:{t.BRONZE};}}"
            )
            self.group.addButton(btn)
            row.addWidget(btn)
            self.buttons[name] = btn
        self.buttons[names[0]].setChecked(True)
        self.group.buttonClicked.connect(lambda button: self.changed.emit(button.text()))

    @property
    def value(self) -> str:
        return self.group.checkedButton().text()


class LookGrid(ImageTileGrid):
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # Reserve room for the scrollbar when a new layout makes it appear.
        w = self.width() - self.verticalScrollBar().sizeHint().width() - 2 * t.HAIR
        cols = max(1, min(6, w // (t.TILE_SIZE[0] + t.HUGE)))
        cell = max(1, w // cols)
        self.setGridSize(QSize(cell, cell * 5 // 8 + t.HUGE + t.MD))


class LookDelegate(QStyledItemDelegate):
    def __init__(self, grid: LookGrid) -> None:
        super().__init__(grid)
        self.grid = grid
        self.looks: list[Look] = []

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return self.grid.gridSize()

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        look = self.looks[index.row()]
        p.save()
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        rect = option.rect.adjusted(t.XS, t.XS, -t.XS, -t.XS)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(rect), t.R_PANEL, t.R_PANEL)
        p.setClipPath(shape)
        p.fillPath(shape, QColor(t.PANEL))
        art = rect.adjusted(0, 0, 0, -(t.HUGE + t.XS))
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if pix and not pix.isNull():
            p.drawPixmap(art, pix)
        else:
            p.fillRect(art, QColor(look.theme.surface))
        left = rect.left() + t.MD
        p.setFont(t.font(t.BODY, bold=True))
        p.setPen(QColor(t.INK))
        title = QRect(left, art.bottom() + t.XS, rect.width() - 2 * t.MD, t.XL)
        p.drawText(
            title,
            Qt.AlignmentFlag.AlignVCenter,
            p.fontMetrics().elidedText(look.name, Qt.TextElideMode.ElideRight, title.width()),
        )
        p.setFont(t.font(t.SMALL))
        p.setPen(QColor(t.GREEN if look.active else t.MUTED))
        p.drawText(left, rect.bottom() - t.SM, look.caption)
        if not look.pack:
            p.setPen(QPen(QColor(t.EDGE), t.HAIR))
            for i, colour in enumerate(
                (look.theme.background, look.theme.surface, look.theme.accent)
            ):
                p.setBrush(QColor(colour))
                p.drawEllipse(
                    rect.right() - t.MD - (3 - i) * t.MD, rect.bottom() - t.LG, t.SM, t.SM
                )
        focus = bool(
            option.state & (QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_Selected)
        )
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setClipping(False)
        p.setPen(
            QPen(
                QColor(t.GOLD if focus else t.BRONZE if hover else t.EDGE),
                t.FOCUS if focus else t.HAIR,
            )
        )
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shape)
        p.restore()


class Gallery(QWidget):
    selected = Signal(object)

    def __init__(self, ctx: AppContext, store: SkinStore) -> None:
        super().__init__()
        self.store = store
        self.game: Path | None = None
        self.scale = True
        self.looks: list[Look] = []
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.setSpacing(t.MD)
        self.hero = Hero(
            "Appearance",
            "Skin gallery",
            "Pick a look for your next duel, or mix your own colours.",
            "skins",
        )
        layout.addWidget(self.hero)
        heading = QHBoxLayout()
        self.count = label("Looks", "heading")
        heading.addWidget(self.count)
        heading.addStretch()
        self.import_file = Button("Import", "quiet", "download")
        self.create = Button("Create your own", "primary", "spark")
        heading.addWidget(self.import_file)
        heading.addWidget(self.create)
        layout.addLayout(heading)
        tools = QHBoxLayout()
        self.filters = Choices(FILTERS)
        self.search = SearchField("Search looks")
        self.search.setMinimumWidth(t.TILE_SIZE[0] - t.XXL)
        tools.addWidget(self.filters)
        tools.addWidget(self.search, 1)
        layout.addLayout(tools)
        self.filters.changed.connect(self.filter)
        self.search.textChanged.connect(self.filter)
        self.grid = LookGrid(ctx.tasks)
        self.grid.setAccessibleName("Looks")
        self.grid.setStyleSheet(f"QListView {{background:{t.CANVAS}; border:none; padding:0;}}")
        self.delegate = LookDelegate(self.grid)
        self.grid.setItemDelegate(self.delegate)
        self.grid.clicked.connect(self._selected)
        self.grid.activated.connect(self._selected)
        layout.addWidget(self.grid, 1)
        self.empty_panel = Panel()
        self.empty_panel.body.addStretch()
        self.empty_panel.body.addWidget(
            glyph("skins", t.BRAND_ICON), 0, Qt.AlignmentFlag.AlignCenter
        )
        self.empty_title = label("", "heading")
        self.empty_panel.body.addWidget(self.empty_title, 0, Qt.AlignmentFlag.AlignCenter)
        self.empty = label("", "body", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_panel.body.addWidget(self.empty)
        browse = Button("Browse all looks", "secondary", "library")
        browse.clicked.connect(self._browse)
        self.empty_panel.body.addWidget(browse, 0, Qt.AlignmentFlag.AlignCenter)
        self.empty_panel.body.addStretch()
        layout.addWidget(self.empty_panel, 1)
        footer = QHBoxLayout()
        self.info = label("", "muted", True)
        footer.addWidget(self.info, 1)
        self.restore = Button("Restore previous pack settings", "quiet")
        self.restore.hide()
        self.refresh = Button("Refresh packs", "quiet")
        footer.addWidget(self.restore)
        footer.addWidget(self.refresh)
        layout.addLayout(footer)
        self.filter()

    def set_collection(self, data: Collection, game: Path | None) -> None:
        self.looks, self.game, self.scale = data.looks, game, data.scale
        self.restore.setVisible(data.restore_pack)
        self.filter()

    def _browse(self) -> None:
        self.search.clear()
        self.filters.buttons["All looks"].click()

    def filter(self) -> None:
        group = self.filters.value
        visible = filter_looks(self.looks, group, self.search.text())
        self.delegate.looks = visible
        self.count.setText(f"{len(visible)} look" + ("" if len(visible) == 1 else "s"))
        self.grid.set_tiles(
            [
                Tile(
                    v.name,
                    Path(v.key),
                    render=partial(self.store.thumbnail, v, self.game, self.scale),
                    tooltip=f"{v.name} · {v.caption}",
                )
                for v in visible
            ]
        )
        self.grid.setVisible(bool(visible))
        message = {
            "Mine": "Your saved themes appear here. Choose Create your own to start.",
            "Downloadable": "No skin packs are available here yet. The built-in looks are ready to use.",
        }.get(group, "No looks match your search.")
        self.empty_title.setText(
            "No matching looks"
            if self.search.text()
            else "Your theme library"
            if group == "Mine"
            else "Downloadable skins"
        )
        self.empty.setText(
            "Try another name or browse all looks." if self.search.text() else message
        )
        self.empty_panel.setVisible(not visible)

    def _selected(self, index: QModelIndex) -> None:
        if self.isVisible() and index.isValid():
            self.selected.emit(self.delegate.looks[index.row()])

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = self.height() < t.HERO_MAX
        self.hero.description.setVisible(not compact)
        self.hero.headline.setStyleSheet(f"font-size:{t.XXL}px;" if compact else "")
        self.hero.setFixedHeight(
            t.TILE_H + t.MD if compact else min(t.HERO_MAX, max(t.TILE_LARGE, self.height() // 4))
        )

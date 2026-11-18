import random
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from sleeves import PATTERNS
from studio import theme as t
from studio.context import AppContext
from studio.icons import icon
from studio.widgets import Button, SearchField, Tile, glyph, label, margins

from .skins_grid import AppearanceGrid
from .sleeves_collection import FAMILIES, SOURCES, Choice, filtered
from .sleeves_widgets import Facet, Menu
from .sleeves_work import Thumbnails, prints


class PrintGrid(AppearanceGrid):
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        available = max(1, self.viewport().width() - 1)
        columns = max(1, available // (t.TILE_LARGE - t.SM))
        self.setGridSize(QSize(available // columns, t.TILE_SIZE[1] + t.XXL))


class PrintDelegate(QStyledItemDelegate):
    def __init__(self, gallery: "Gallery") -> None:
        super().__init__(gallery.grid)
        self.gallery = gallery

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return self.gallery.grid.gridSize()

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item = self.gallery.visible[index.row()]
        p.save()
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        rect = option.rect.adjusted(t.XS, t.XS, -t.XS, -t.XS)
        active = bool(
            option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        )
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.setPen(QPen(QColor(t.GOLD if active else t.EDGE), t.FOCUS if active else t.HAIR))
        p.setBrush(QColor(t.ACTIVE if active else t.HOVER if hover else t.PANEL))
        p.drawRoundedRect(rect, t.R_SMALL, t.R_SMALL)
        box = rect.adjusted(t.SM, t.MD, -t.SM, -t.HUGE)
        img = index.data(Qt.ItemDataRole.DecorationRole)
        if img and not img.isNull():
            target = box.adjusted(0, 0, 0, 0)
            target.setSize(img.size().scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio))
            target.moveCenter(box.center())
            p.drawPixmap(target, img)
        else:
            icon("sleeves", t.BRONZE).paint(p, box)
        copy = rect.adjusted(t.MD, rect.height() - t.HUGE + t.XS, -t.MD, -t.XS)
        p.setPen(QColor(t.INK))
        p.setFont(t.font(t.BODY, bold=True))
        p.drawText(
            copy,
            Qt.AlignmentFlag.AlignTop,
            p.fontMetrics().elidedText(item.name, Qt.TextElideMode.ElideRight, copy.width()),
        )
        p.setPen(QColor(t.GOLD if active else t.MUTED))
        p.setFont(t.font(t.SMALL))
        p.drawText(copy.adjusted(0, t.ICON, 0, 0), Qt.AlignmentFlag.AlignTop, item.detail)
        p.restore()


class Gallery(QWidget):
    picked = Signal(object)
    opened = Signal(str)

    def __init__(self, ctx: AppContext, thumbs: Thumbnails) -> None:
        super().__init__()
        self.ctx, self.thumbs = ctx, thumbs
        self.measuring = None
        self.measure_generation = 0
        self.items: list[Choice] = []
        self.visible: list[Choice] = []
        self.related: tuple[str, Choice] | None = None
        self.measured: dict[Path, str] = {}
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.SM)
        header = QHBoxLayout()
        header.addWidget(label("Collection", "heading"))
        self.count = label("", "muted")
        header.addWidget(self.count)
        header.addStretch()
        self.more = Button("Get more", "quiet", "download")
        self.more.setAccessibleName("Get more sleeves")
        header.addWidget(self.more)
        self.actions = {}
        for key, title in (
            ("design", "Design"),
            ("card", "From a card"),
            ("image", "From an image"),
            ("manage", "Manage"),
        ):
            button = Button(title, "secondary" if key != "manage" else "quiet")
            button.clicked.connect(lambda checked=False, view=key: self.opened.emit(view))
            header.addWidget(button)
            self.actions[key] = button
        body.addLayout(header)
        filters = QHBoxLayout()
        self.search = SearchField("Search pattern, palette or tags")
        self.source = Menu(
            "Filter by source",
            (
                "All sleeves",
                "Favorites",
                "Recently used",
                "Ready-made",
                "Saved sleeves",
                "Pack sleeve",
                *SOURCES.values(),
            ),
        )
        self.colour = Facet("Filter by color", ("All colors", *FAMILIES))
        self.family = Facet(
            "Pattern family",
            ("All families", *sorted({p.family for p in PATTERNS.values() if p.family})),
        )
        self.surprise = Button("Surprise me", "secondary", "spark")
        filters.addWidget(self.search, 3)
        filters.addWidget(self.source, 2)
        filters.addWidget(self.family, 2)
        filters.addWidget(self.colour, 2)
        filters.addWidget(self.surprise)
        body.addLayout(filters)
        self.grid = PrintGrid(ctx.tasks, thumbs)
        self.grid.setAccessibleName("Sleeve collection")
        self.grid.setStyleSheet(
            f"QListView {{ background:{t.CANVAS}; padding:0; border:0; }} QListView:focus {{border:{t.HAIR}px solid {t.PALE};}}"
        )
        self.grid.setItemDelegate(PrintDelegate(self))
        body.addWidget(self.grid, 1)
        self.empty = QWidget()
        blank = QVBoxLayout(self.empty)
        blank.setSpacing(t.SM)
        blank.addStretch()
        blank.addWidget(glyph("search", t.BRAND_ICON), 0, Qt.AlignmentFlag.AlignCenter)
        blank.addWidget(label("No matching sleeves", "heading"), 0, Qt.AlignmentFlag.AlignCenter)
        blank.addWidget(
            label("Try another search or browse the whole collection.", "muted"),
            0,
            Qt.AlignmentFlag.AlignCenter,
        )
        self.clear = Button("Clear filters")
        self.clear.clicked.connect(self.clear_filters)
        blank.addWidget(self.clear, 0, Qt.AlignmentFlag.AlignCenter)
        blank.addStretch()
        body.addWidget(self.empty, 1)
        self.grid.clicked.connect(self.choose)
        self.grid.activated.connect(self.choose)
        self.surprise.clicked.connect(self.random)
        self.search.textChanged.connect(self.search_changed)
        self.family.currentTextChanged.connect(self.filter)
        self.source.currentTextChanged.connect(self.filter)
        self.colour.currentTextChanged.connect(self.filter)
        self.filter()

    def clear_filters(self) -> None:
        self.related = None
        with QSignalBlocker(self.family), QSignalBlocker(self.source), QSignalBlocker(self.colour):
            self.family.setCurrentIndex(0)
            self.source.setCurrentIndex(0)
            self.colour.setCurrentIndex(0)
        with QSignalBlocker(self.search):
            self.search.clear()
        self.filter()
        self.search.setFocus()

    def search_changed(self) -> None:
        self.related = None
        self.filter()

    def more_like(self, kind: str, item: Choice | None) -> None:
        if not item or not item.recipe:
            return
        self.related = kind, item
        with (
            QSignalBlocker(self.family),
            QSignalBlocker(self.source),
            QSignalBlocker(self.colour),
            QSignalBlocker(self.search),
        ):
            self.family.setCurrentIndex(0)
            self.colour.setCurrentIndex(0)
            self.source.setCurrentText("Ready-made")
            self.search.setText(
                PATTERNS[item.recipe.pattern].name if kind == "pattern" else item.palette
            )
        self.filter()
        self.grid.scrollToTop()
        self.search.setFocus()

    def choose(self, index: QModelIndex) -> None:
        if index.isValid():
            self.picked.emit(self.visible[index.row()])

    def random(self) -> None:
        if self.visible:
            idx = self.grid.model().index(random.randrange(len(self.visible)), 0)
            self.grid.setCurrentIndex(idx)
            self.grid.setFocus()
            self.choose(idx)
            self.grid.scrollTo(idx, self.grid.ScrollHint.PositionAtCenter)
            # Staging a print adds footer actions, resizing the viewport on the next layout.
            QTimer.singleShot(0, self.grid, lambda: self.reveal_choice(idx))

    def reveal_choice(self, index: QModelIndex) -> None:
        if self.grid.currentIndex() == index and self.grid.isVisible():
            self.grid.scrollTo(index, self.grid.ScrollHint.EnsureVisible)

    def set_items(self, items: list[Choice]) -> None:
        self.grid.invalidate()
        previous = {item.key: item.entry.image for item in self.items if item.entry}
        self.measured = {
            item.key: self.measured[item.key]
            for item in items
            if item.key in self.measured and previous.get(item.key) == item.entry.image
        }
        self.items = [*items, *prints().values()]
        self.filter()

    def filter(self) -> None:
        items = filtered(
            self.items, self.search.text(), self.source.currentText(), "All colors", self.measured
        )
        if self.related:
            kind, seed = self.related
            items = [
                c
                for c in items
                if c.recipe
                and (
                    c.recipe.pattern == seed.recipe.pattern and c.palette != seed.palette
                    if kind == "pattern"
                    else c.palette == seed.palette and c.recipe.pattern != seed.recipe.pattern
                )
            ]
        families: Counter[str] = Counter()
        colors: Counter[str] = Counter()
        family, colour = self.family.currentText(), self.colour.currentText()
        previous = self.visible
        self.visible = []
        for item in items:
            group = item.family or self.measured.get(item.key, "")
            in_colour = not self.colour.currentIndex() or group == colour
            in_family = not self.family.currentIndex() or item.motif == family
            if in_colour:
                families[item.motif] += 1
            if in_family:
                colors[group] += 1
            if in_colour and in_family:
                self.visible.append(item)
        self.family.set_counts(families)
        self.colour.set_counts(colors)
        if self.visible != previous:
            self.grid.set_tiles([Tile(c.name, c.key, tooltip=c.tooltip) for c in self.visible])
        else:
            self.grid.viewport().update()
        self.count.setText(f"{len(self.visible):,}")
        self.grid.setVisible(bool(self.visible))
        self.empty.setVisible(not self.visible)
        self.surprise.setEnabled(bool(self.visible))
        if self.colour.currentIndex() > 0:
            self.measure()

    def stop_measurement(self) -> None:
        if self.measuring:
            self.measuring.cancel()
            self.measuring = None

    def measure(self) -> None:
        if self.measuring:
            return
        missing = [
            item
            for item in self.items
            if item.entry and not item.family and item.key not in self.measured
        ]
        if not missing:
            return
        self.measure_generation += 1
        generation = self.measure_generation
        job = self.measuring = self.ctx.tasks.start(
            lambda worker: self.thumbs.colours(missing, worker)
        )

        def ready(colours: dict[Path, str]) -> None:
            if not self.measuring or generation != self.measure_generation:
                return
            self.measuring = None
            current = {item.key: item for item in self.items}
            self.measured.update(
                {
                    item.key: colours.get(item.key, "")
                    for item in missing
                    if current.get(item.key) == item
                }
            )
            index = self.grid.currentIndex()
            selected = self.visible[index.row()] if index.isValid() else None
            self.filter()
            # Reading the colors of local artwork can insert rows ahead of a player's choice.
            if selected and not self.grid.currentIndex().isValid() and selected in self.visible:
                self.grid.setCurrentIndex(self.grid.model().index(self.visible.index(selected), 0))

        def failed(_: Exception) -> None:
            if not self.measuring or generation != self.measure_generation:
                return
            self.measuring = None
            self.ctx.notify("Could not read a saved sleeve’s colors.")

        job.result.connect(ready)
        job.error.connect(failed)

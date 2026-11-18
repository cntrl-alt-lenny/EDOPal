"""Deck-library controls built from Studio surfaces and colour tokens."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from decks import Preview
from studio import theme as t
from studio.icons import icon
from studio.pages.mydecks_logic import LibraryRow, breakdown, card_name
from studio.tasks import TaskRunner
from studio.widgets import Button, ElidedLabel, Hero, ImageTileGrid, Panel, Tile, label, margins

ALIGN = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
KINDS = {"monster": t.GOLD, "spell": t.GREEN, "trap": t.RED, "other": t.TEXT, "unresolved": t.MUTED}


class DeckHero(Hero):
    def __init__(self, description: str) -> None:
        super().__init__("Personal collection", "My decks", description, "check")
        self.sleeves = [
            QPixmap(str(t.ASSETS / "window/gallery" / filename))
            for filename in ("sleeve-1.png", "sleeve-0.png")
        ]
        self.setFixedHeight(t.TILE_LARGE + t.XL)

    def _print(self, p: QPainter, x: float, y: float, height: float) -> None:
        size = min(height * 1.38, self.width() * 0.23)
        for offset, angle, sleeve in zip(
            (-0.47, 0.46, 0), (-17, 17, -3), (*self.sleeves, self.sleeves[1]), strict=True
        ):
            p.save()
            p.translate(x + offset * size, y + abs(offset) * t.XL)
            p.rotate(angle)
            box = QRectF(-size * 0.34, -size * 0.5, size * 0.68, size)
            p.setPen(QPen(QColor(t.BRONZE), t.FOCUS))
            p.drawRoundedRect(box, t.R_SMALL, t.R_SMALL)
            p.drawPixmap(box, sleeve, QRectF(sleeve.rect()))
            p.restore()


class Screen(QWidget):
    def __init__(self, title: str = "", back: Callable[[], object] | None = None) -> None:
        super().__init__()
        self.body = QVBoxLayout(self)
        self.setMaximumWidth(t.CONTENT_MAX)
        margins(self.body, t.XL)
        self.body.setSpacing(t.LG)
        self.initial: QWidget | None = None
        if back:
            row = QHBoxLayout()
            self.back = button("Back", back, "quiet", "back")
            row.addWidget(self.back)
            row.addWidget(label("MY DECKS", "goldKicker"))
            row.addStretch()
            self.body.addLayout(row)
            self.initial = self.back
        if title:
            heading = ElidedLabel()
            heading.setProperty("role", "display")
            heading.set_value(title)
            self.body.addWidget(heading)

    def panel(self) -> Panel:
        panel = Panel(t.LG)
        self.body.addWidget(panel, 1)
        return panel


def button(
    text: str, slot: Callable[[], object], role: str = "secondary", symbol: str = ""
) -> Button:
    btn = Button(text, role, symbol)
    btn.clicked.connect(lambda: slot())
    return btn


class Choice(QComboBox):
    def paintEvent(self, event: QPaintEvent) -> None:
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        p = QStylePainter(self)
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        area = self.rect().adjusted(t.MD, 0, -t.XXL, 0)
        paint_text(p, area, self.currentText(), t.INK, t.BODY)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(t.GOLD), t.FOCUS))
        x, y = self.width() - t.LG, self.height() // 2
        p.drawPolyline(
            [
                QPoint(x - t.XS, y - t.HAIR),
                QPoint(x, y + t.XS - t.HAIR),
                QPoint(x + t.XS, y - t.HAIR),
            ]
        )


def choice(title: str, items: Iterable[str]) -> QComboBox:
    box = Choice()
    box.setAccessibleName(title)
    box.setToolTip(title)
    box.addItems(list(items))
    box.setMinimumHeight(t.BUTTON_H)
    box.setMinimumWidth(t.TILE_LARGE - t.XL)
    box.currentTextChanged.connect(lambda text: box.setToolTip(f"{title} · {text}"))
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    box.setStyleSheet(f"""
        QComboBox {{ background: {t.RAISED}; border: {t.HAIR}px solid {t.EDGE};
            border-radius: {t.R_SMALL}px; padding: {t.SM}px {t.MD}px; }}
        QComboBox:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}
        QComboBox::drop-down {{ border: 0; width: {t.XL}px; }}
        QAbstractItemView {{ background: {t.PANEL}; selection-background-color: {t.ACTIVE};
            selection-color: {t.PALE}; }}
    """)
    return box


class RowModel(QAbstractListModel):
    def __init__(self, parent: QListView) -> None:
        super().__init__(parent)
        self.rows: list[LibraryRow] = []

    def replace(self, rows: list[LibraryRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return ", ".join(
                filter(None, (row.deck.name, row.folder, row.counts, row.changed, row.problem))
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{row.deck.path}\n{breakdown(row.deck.preview) if row.deck.preview else row.deck.error}"
        return None


def paint_text(
    p: QPainter, rect: QRect, value: str, colour: str, size: int, bold: bool = False
) -> None:
    p.setFont(t.font(size, bold=bold))
    p.setPen(QColor(colour))
    p.drawText(
        rect, ALIGN, p.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, rect.width())
    )


class DeckDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(t.TILE_LARGE, t.HUGE + t.XL)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.model().rows[index.row()]
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        p.fillRect(r, QColor(t.ACTIVE if active else t.PANEL))
        p.setPen(QColor(t.EDGE))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        left, top, width = r.left() + t.MD, r.top(), r.width()
        mark = QRect(left, top + t.MD, t.XXL, t.HUGE)
        p.setBrush(QColor(t.RAISED))
        p.setPen(QColor(t.EDGE))
        p.drawRoundedRect(mark, t.R_SMALL, t.R_SMALL)
        icon("warning" if row.problem else "deck", t.RED if row.problem else t.GOLD).paint(
            p, mark.adjusted(t.XS, t.SM, -t.XS, -t.SM)
        )
        left = mark.right() + t.LG
        stats = r.right() - t.RAIL_COMPACT - t.MD
        when = stats - t.TILE_LARGE
        title_end = when if width >= t.HERO_MAX + t.TILE_LARGE else stats
        paint_text(
            p,
            QRect(left, top + t.SM, title_end - left - t.SM, t.XL),
            row.deck.name,
            t.INK,
            t.SUBTITLE,
            True,
        )
        folder = row.folder + (f"   ·   {row.problem}" if row.problem else "")
        if title_end == stats:
            folder += f"   ·   {row.changed}"
        paint_text(
            p,
            QRect(left, top + t.XXL, title_end - left - t.SM, t.XL),
            folder,
            t.RED if row.problem else t.MUTED,
            t.SMALL,
        )
        if title_end != stats:
            paint_text(p, QRect(when, top, t.TILE_LARGE, r.height()), row.changed, t.MUTED, t.SMALL)
        paint_text(p, QRect(stats, top + t.SM, t.RAIL_COMPACT, t.XXL), row.counts, t.TEXT, t.SMALL)
        if preview := row.deck.preview:
            start = stats
            for kind, n in preview.breakdown.items():
                length = round(t.TILE_LARGE * n / max(1, sum(preview.counts)))
                if length:
                    p.fillRect(QRect(start, top + t.HUGE - t.XS, length, t.XS), QColor(KINDS[kind]))
                    start += length
        icon("chevron", t.MUTED).paint(
            p, r.right() - t.XL, top + (r.height() - t.LG) // 2, t.LG, t.LG
        )
        p.restore()


class CardDelegate(QStyledItemDelegate):
    def __init__(self, grid: CardGrid) -> None:
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return self.grid.gridSize()

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index: QModelIndex) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = opt.rect.adjusted(t.XS, t.XS, -t.XS, -t.XS)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        p.setBrush(QColor(t.ACTIVE if selected else t.RAISED))
        p.setPen(QColor(t.GOLD if selected else t.EDGE))
        p.drawRoundedRect(box, t.R_SMALL, t.R_SMALL)
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        left = box.x() + t.SM
        art = QRect(left, box.y() + t.XS, t.BUTTON_H, box.height() - 2 * t.XS)
        if pix and not pix.isNull():
            size = pix.size().scaled(art.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(art)
            target.setSize(size)
            target.moveCenter(art.center())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(target, pix)
            left = art.right() + t.SM
        else:
            p.fillRect(
                QRect(left, art.y() + t.SM, t.XS, art.height() - 2 * t.SM),
                QColor(self.grid.colours[index.row()]),
            )
            left += t.MD
        width = box.right() - left - t.XS
        paint_text(p, QRect(left, box.y() + t.SM, width, t.XL), index.data(), t.INK, t.BODY, True)
        paint_text(
            p,
            QRect(left, box.y() + t.XXL, width, t.XL),
            self.grid.details[index.row()],
            t.MUTED,
            t.SMALL,
        )
        p.restore()


class CardGrid(ImageTileGrid):
    inspected = Signal(str)

    def __init__(self, runner: TaskRunner, game: Path, preview: Preview) -> None:
        super().__init__(runner)
        self.game, self.preview = game, preview
        self.details: list[str] = []
        self.colours: list[str] = []
        self.setAccessibleName("Cards in this section")
        self.setItemDelegate(CardDelegate(self))
        self.setMinimumHeight(t.TILE_H)
        self.setStyleSheet(
            f"QListView {{ background: {t.PANEL}; padding: 0; border: 0; }} "
            f"QListView:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}"
        )
        self.selectionModel().currentChanged.connect(self._tip)
        self.activated.connect(self._tip)

    def _tip(self, idx: QModelIndex) -> None:
        if idx.isValid():
            detail = f"{idx.data()} · {self.details[idx.row()]}"
            self.setAccessibleDescription(detail)
            self.inspected.emit(detail)

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            idx = self.indexAt(event.pos())
            if idx.isValid():
                QToolTip.showText(
                    event.globalPos(), f"{idx.data()} · {self.details[idx.row()]}", self
                )
                return True
        return super().viewportEvent(event)

    def show_section(self, section: int) -> None:
        self.details.clear()
        self.colours.clear()
        tiles = []
        for code, copies in Counter(self.preview.sections[section]).items():
            card = self.preview.cards.get(code)
            name = card_name(self.preview, code)
            self.details.append(
                " · ".join(
                    filter(
                        None,
                        (
                            card.type_line if card else "Missing from game data",
                            f"{card.rating_kind} {card.rating}"
                            if card and card.rating_kind and card.rating is not None
                            else "",
                            card.attribute.title() if card else "",
                        ),
                    )
                )
            )
            self.colours.append(KINDS[card.kind if card else "unresolved"])
            tiles.append(Tile(f"{copies} × {name}", self.game / "pics" / f"{code}.jpg"))
        self.set_tiles(tiles)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        width = max(1, self.viewport().width() - t.SM - t.FOCUS)
        cols = max(1, width // (t.RAIL_WIDTH + t.HUGE))
        self.setGridSize(QSize(width // cols, t.HUGE + t.XL))


class Contents(Panel):
    def __init__(self, runner: TaskRunner, game: Path, preview: Preview) -> None:
        super().__init__(t.LG)
        row = QHBoxLayout()
        self.sections = QButtonGroup(self)
        for i, (s, n) in enumerate(zip(preview.sections._fields, preview.counts, strict=True)):
            btn = Button(f"{s.title()}  {n}")
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"QPushButton:checked {{background: {t.ACTIVE}; color: {t.PALE}; border-color: {t.GOLD};}}"
            )
            self.sections.addButton(btn, i)
            row.addWidget(btn)
        self.sections.button(0).setChecked(True)
        mix = label(breakdown(preview), "muted", True)
        mix.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(mix, 1)
        self.body.addLayout(row)
        self.grid = CardGrid(runner, game, preview)
        self.empty: QLabel = label("There are no cards in this section.", "body")
        self.body.addWidget(self.empty)
        self.body.addWidget(self.grid, 1)
        selected = ElidedLabel()
        self.body.addWidget(selected)
        selected.hide()

        def inspect(text: str) -> None:
            selected.set_value(text)
            selected.show()

        self.grid.inspected.connect(inspect)

        def show(section: int) -> None:
            self.grid.show_section(section)
            self.empty.setVisible(not preview.sections[section])
            selected.hide()

        self.sections.idClicked.connect(show)
        show(0)

"""Render Main, Extra and Side together in one virtualized, scrollable list."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QResizeEvent, QTextLayout, QTextOption
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from studio import theme as t
from studio.pages.decks_preview_data import GROUPS, PreviewCard, PreviewSection
from studio.tasks import TaskRunner
from studio.widgets import Panel, Tile
from studio.widgets.tiles import _read_thumbnail, _Tiles

TYPE_NAMES = {
    "Monster": ("monster", "monsters"),
    "Spell": ("spell", "spells"),
    "Trap": ("trap", "traps"),
    "Other": ("other card", "other cards"),
    "Unknown type": ("card of unknown type", "cards of unknown type"),
}


def paint_text(
    painter: QPainter,
    rect: QRect,
    text: str,
    colour: str,
    size: int,
    bold: bool = False,
) -> None:
    painter.setFont(t.font(size, bold=bold))
    painter.setPen(QColor(colour))
    line = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
    painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line)


def wrapped_name(title: str, width: int) -> tuple[str, ...]:
    layout = QTextLayout(title, t.font(t.SMALL, bold=True))
    options = QTextOption()
    options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(options)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(width)
    # Qt reports UTF-16 code units, which differ from Python character offsets.
    split = line.textLength() * 2
    layout.endLayout()
    encoded = title.encode("utf-16-le")
    if split >= len(encoded):
        return (title,)
    return (
        encoded[:split].decode("utf-16-le").rstrip(),
        encoded[split:].decode("utf-16-le").lstrip(),
    )


@dataclass(frozen=True)
class CardRow:
    title: str = ""
    subtitle: str = ""
    cards: tuple[PreviewCard, ...] = ()
    columns: int = 1
    artwork: bool = False

    @property
    def height(self) -> int:
        if self.title:
            return t.XXL + t.LG if self.subtitle else t.XXL
        return t.HUGE + t.MD if self.artwork else t.XL + t.XS

    @property
    def text(self) -> str:
        if self.title:
            return " · ".join(part for part in (self.title, self.subtitle) if part)
        return "\n".join(
            " · ".join(part for part in (f"{card.copies} × {card.name}", card.detail) if part)
            for card in self.cards
        )


class SectionRows(QAbstractListModel):
    def __init__(self, parent: QListView) -> None:
        super().__init__(parent)
        self.rows: list[CardRow] = []
        self.columns: tuple[int, int] | None = None

    def reflow(
        self, sections: tuple[PreviewSection, ...], columns: tuple[int, int], *, reset: bool
    ) -> None:
        if not reset and columns == self.columns:
            return
        self.columns = columns
        self.beginResetModel()
        self.rows.clear()
        for i, (group, section) in enumerate(zip(GROUPS, sections, strict=False)):
            count = section.count
            title = f"{group}  ·  {count} {'card' if count == 1 else 'cards'}"
            subtitle = (
                "   ·   ".join(f"{n} {TYPE_NAMES[kind][n != 1]}" for kind, n in section.type_counts)
                if i == 0
                else ""
            )
            self.rows.append(
                CardRow(title, subtitle or ("No cards in this section" if not count else ""))
            )
            for artwork, per_row in zip((True, False), columns, strict=True):
                cards = tuple(card for card in section.cards if bool(card.artwork) is artwork)
                self.rows.extend(
                    CardRow(cards=cards[start : start + per_row], columns=per_row, artwork=artwork)
                    for start in range(0, len(cards), per_row)
                )
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.AccessibleTextRole,
            Qt.ItemDataRole.ToolTipRole,
        ):
            return row.text
        return None


class SectionDelegate(QStyledItemDelegate):
    def __init__(self, view: CardList) -> None:
        super().__init__(view)
        self.view = view

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        row: CardRow = index.data(Qt.ItemDataRole.UserRole)
        return QSize(1, row.height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row: CardRow = index.data(Qt.ItemDataRole.UserRole)
        rect = option.rect
        painter.save()
        if row.title:
            painter.fillRect(rect.adjusted(0, t.XS, 0, 0), QColor(t.RAIL))
            paint_text(
                painter,
                rect.adjusted(t.SM, t.XS, -t.SM, -t.LG if row.subtitle else 0),
                row.title,
                t.PALE,
                t.BODY,
                True,
            )
            if row.subtitle:
                mix = rect.adjusted(t.SM, t.XXL - t.SM, -t.SM, -t.XS)
                paint_text(painter, mix, row.subtitle, t.MUTED, t.SMALL)
        else:
            self._cards(painter, rect, row, bool(option.state & QStyle.StateFlag.State_HasFocus))
        painter.restore()

    def _cards(self, painter: QPainter, rect: QRect, row: CardRow, focused: bool) -> None:
        cell_width = rect.width() // row.columns
        for i, card in enumerate(row.cards):
            cell = QRect(rect.x() + i * cell_width, rect.y(), cell_width, rect.height())
            cell.adjust(t.XS, t.XS if row.artwork else 0, -t.XS, 0)
            if row.artwork:
                self._thumbnail(painter, cell, card)
            else:
                painter.fillRect(cell.adjusted(0, 0, 0, -t.HAIR), QColor(t.RAISED))
                count = f"×{card.copies}"
                painter.setFont(t.font(t.SMALL, bold=True))
                count_width = painter.fontMetrics().horizontalAdvance(count) + t.SM
                name = cell.adjusted(t.SM, 0, -count_width - t.SM, 0)
                paint_text(painter, name, card.name, t.INK, t.SMALL)
                paint_text(
                    painter,
                    cell.adjusted(cell.width() - count_width - t.XS, 0, 0, 0),
                    count,
                    t.PALE,
                    t.SMALL,
                    True,
                )
        if focused:
            painter.setPen(QColor(t.GOLD))
            painter.drawRect(rect.adjusted(0, 0, -t.HAIR, -t.HAIR))

    def _thumbnail(self, painter: QPainter, cell: QRect, card: PreviewCard) -> None:
        painter.fillRect(cell, QColor(t.RAISED))
        art = QRect(cell.x() + t.XS, cell.y() + t.XS, t.XXL, cell.height() - 2 * t.XS)
        pixmap = self.view.thumbnail(card.artwork)
        left = cell.left() + t.SM
        if pixmap and not pixmap.isNull():
            target = QRect(art)
            target.setSize(pixmap.size().scaled(art.size(), Qt.AspectRatioMode.KeepAspectRatio))
            target.moveCenter(art.center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(target, pixmap)
            left = art.right() + t.SM
        width = cell.right() - left - t.XS
        lines = wrapped_name(card.name, width)
        y = cell.center().y() - (len(lines) + 1) * t.LG // 2
        for line in lines:
            paint_text(painter, QRect(left, y, width, t.LG), line, t.INK, t.SMALL, True)
            y += t.LG
        paint_text(painter, QRect(left, y, width, t.LG), f"×{card.copies}", t.PALE, t.SMALL)


class CardList(QListView):
    def __init__(self, runner: TaskRunner) -> None:
        super().__init__()
        self.sections: tuple[PreviewSection, ...] = ()
        self.rows = SectionRows(self)
        self.thumbnails = _Tiles(self, runner, _read_thumbnail)
        self.paths: dict[Path, int] = {}
        self.setModel(self.rows)
        self.setItemDelegate(SectionDelegate(self))
        self.setAccessibleName("Main, Extra and Side deck cards")
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(t.TILE_H)
        self.setStyleSheet(
            f"QListView {{ background: {t.PANEL}; border: 0; }} "
            f"QListView:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}"
        )

    def thumbnail(self, path: Path | None) -> QPixmap | None:
        if path in self.paths:
            index = self.thumbnails.index(self.paths[path], 0)
            return index.data(Qt.ItemDataRole.DecorationRole)
        return None

    def set_sections(self, sections: tuple[PreviewSection, ...]) -> None:
        self.sections = sections
        paths = dict.fromkeys(
            card.artwork for section in sections for card in section.cards if card.artwork
        )
        self.paths = {path: i for i, path in enumerate(paths)}
        # Cached thumbnails belong to the path, even when the player changes decks.
        cached = self.thumbnails.cache.copy()
        self.thumbnails.replace(tuple(Tile("", path) for path in paths))
        self.thumbnails.cache.update(cached)
        self._reflow(reset=True)
        self.scrollToTop()

    def _reflow(self, *, reset: bool = False) -> None:
        width = self.width() - self.verticalScrollBar().sizeHint().width()
        cols = max(1, width // (t.TILE_LARGE + t.SM)), max(1, width // (t.TILE_LARGE + t.XXL))
        self.rows.reflow(self.sections, cols, reset=reset)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reflow()


class DeckContents(Panel):
    def __init__(self, runner: TaskRunner, *, inset: int = t.LG) -> None:
        super().__init__(inset)
        self.cards = CardList(runner)
        self.body.addWidget(self.cards)

    def set_sections(self, sections: tuple[PreviewSection, ...]) -> None:
        self.cards.set_sections(sections)

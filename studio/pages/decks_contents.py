"""The card browser shared by published previews and the personal library."""

from pathlib import Path

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QResizeEvent, QTextLayout, QTextOption
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from studio import theme as t
from studio.icons import icon
from studio.pages.decks_preview_data import GROUPS, PreviewCard, PreviewSection
from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import ElidedLabel, ImageTileGrid, Panel, SegmentedControl, Tile, label
from studio.widgets.tiles import _read_thumbnail

CARD_COLOURS = {"Monster": t.GOLD, "Spell": t.GREEN, "Trap": t.RED}

TYPE_NAMES = {
    "Monster": ("monster", "monsters"),
    "Spell": ("spell", "spells"),
    "Trap": ("trap", "traps"),
    "Other": ("other card", "other cards"),
    "Unknown type": ("card of unknown type", "cards of unknown type"),
}


def _read_card(path: Path, worker: WorkerContext) -> QImage:
    image = _read_thumbnail(path, worker)
    if image.isNull() and path.suffix == ".jpg":
        return _read_thumbnail(path.with_suffix(".png"), worker)
    return image


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


def _title_lines(title: str, width: int) -> tuple[str, ...]:
    layout = QTextLayout(title, t.font(t.BODY, bold=True))
    options = QTextOption()
    options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(options)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(width)
    split = line.textLength() * 2
    layout.endLayout()
    encoded = title.encode("utf-16-le")
    if split >= len(encoded):
        return (title,)
    return encoded[:split].decode("utf-16-le").rstrip(), encoded[split:].decode(
        "utf-16-le"
    ).lstrip()


class CardDelegate(QStyledItemDelegate):
    def __init__(self, grid: "CardGrid") -> None:
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
        art = QRect(box.x() + t.SM, box.y() + t.SM, t.XXL + t.SM, box.height() - 2 * t.SM)
        card = self.grid.cards[index.row()]
        colour = CARD_COLOURS.get(card.types[0][0], t.MUTED) if card.types else t.MUTED
        pix = index.data(Qt.ItemDataRole.DecorationRole) if card.artwork else None
        if pix and not pix.isNull():
            target = QRect(art)
            target.setSize(pix.size().scaled(art.size(), Qt.AspectRatioMode.KeepAspectRatio))
            target.moveCenter(art.center())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(target, pix)
        else:
            p.setBrush(QColor(t.RAIL))
            p.setPen(QColor(colour))
            p.drawRoundedRect(art, t.XS, t.XS)
            icon("sleeves", colour).paint(
                p, art.center().x() - t.ICON // 2, art.center().y() - t.ICON // 2, t.ICON, t.ICON
            )
        left = art.right() + t.SM
        width = box.right() - left - t.SM
        lines = _title_lines(index.data(), width)
        top = box.center().y() - (len(lines) + 1) * t.LG // 2
        for line in lines:
            paint_text(p, QRect(left, top, width, t.LG), line, t.INK, t.BODY, True)
            top += t.LG
        paint_text(
            p,
            QRect(left, top, width, t.LG),
            self.grid.description(card),
            colour,
            t.SMALL,
        )
        p.restore()


class CardGrid(ImageTileGrid):
    def __init__(self, runner: TaskRunner) -> None:
        super().__init__(runner, _read_card)
        self.cards: tuple[PreviewCard, ...] = ()
        self.setAccessibleName("Cards in this section")
        self.setItemDelegate(CardDelegate(self))
        self.setMinimumHeight(t.TILE_H)
        self.setStyleSheet(
            f"QListView {{ background: {t.PANEL}; padding: 0; border: 0; }} "
            f"QListView:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}"
        )

    @staticmethod
    def description(card: PreviewCard) -> str:
        if card.detail:
            return card.detail
        return " · ".join(kind for kind, _ in card.types)

    def show_cards(self, cards: tuple[PreviewCard, ...]) -> None:
        self.cards = cards
        self.set_tiles(
            [
                Tile(
                    f"{card.copies} × {card.name}",
                    card.artwork or Path(),
                    self.description(card),
                    tooltip=f"{card.copies} × {card.name} · {self.description(card)}",
                )
                for card in cards
            ]
        )
        self.scrollToTop()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        width = max(1, self.viewport().width() - t.HAIR)
        cols = max(1, width // (t.TILE_LARGE + 2 * t.XXL))
        self.setGridSize(QSize(width // cols, t.HUGE + t.XXL))


class DeckContents(Panel):
    def __init__(self, runner: TaskRunner) -> None:
        super().__init__(t.LG)
        self.data: tuple[PreviewSection, ...] = ()
        self.sections = SegmentedControl(
            [
                (name, "0 cards", symbol)
                for name, symbol in zip(GROUPS, ("deck", "cards", "sleeves"), strict=True)
            ]
        )
        self.body.addWidget(self.sections)
        self.types = label("", "muted", True)
        self.body.addWidget(self.types)
        self.empty = label("There are no cards in this section.", "muted")
        self.body.addWidget(self.empty)
        self.grid = CardGrid(runner)
        self.body.addWidget(self.grid, 1)
        self.inspected = ElidedLabel()
        self.body.addWidget(self.inspected)
        self.inspected.hide()
        self.sections.changed.connect(self.show_section)
        self.grid.selectionModel().currentChanged.connect(self._inspect)
        self.grid.activated.connect(self._inspect)

    def _inspect(self, index: QModelIndex) -> None:
        self.inspected.setVisible(index.isValid())
        if index.isValid():
            self.inspected.set_value(index.data(Qt.ItemDataRole.ToolTipRole))

    def set_sections(self, sections: tuple[PreviewSection, ...]) -> None:
        self.data = sections
        for button, section in zip(self.sections.buttons, sections, strict=True):
            count = f"{section.count} {'card' if section.count == 1 else 'cards'}"
            button.hint.setText(count)
            button.setAccessibleDescription(count)
        if self.sections.buttons[0].isChecked():
            self.show_section(0)
        else:
            self.sections.select(0)

    def show_section(self, index: int) -> None:
        section = self.data[index]
        self.types.setText(
            "   ·   ".join(
                f"{count} {TYPE_NAMES[kind][count != 1]}" for kind, count in section.type_counts
            )
        )
        self.types.setVisible(bool(section.cards))
        self.grid.show_cards(section.cards)
        self.inspected.hide()
        self.empty.setVisible(not section.cards)

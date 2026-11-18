"""Deck-library controls built from Studio surfaces and colour tokens."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from decks import Preview
from studio import theme as t
from studio.icons import icon
from studio.pages.decks_contents import DeckContents, paint_text
from studio.pages.mydecks_logic import LibraryRow, breakdown, saved_sections
from studio.tasks import TaskRunner
from studio.widgets import Button, ElidedLabel, Hero, Panel

KINDS = {"monster": t.GOLD, "spell": t.GREEN, "trap": t.RED, "other": t.TEXT, "unresolved": t.MUTED}


class DeckHero(Hero):
    def __init__(self, description: str) -> None:
        super().__init__("Deck library", "My decks", description, "mydecks")
        self.setFixedHeight(t.TILE_LARGE + t.XL)

    def _print(self, p: QPainter, x: float, y: float, height: float) -> None:
        scale = min(height * 1.20, self.width() * 0.23) / 100
        for offset, angle, symbol, accent in (
            (-52, -20, "spark", t.GREEN),
            (52, 20, "health", t.RED),
            (0, -4, "mydecks", t.GOLD),
        ):
            p.save()
            p.translate(x + offset * scale, y + (8 if offset else 0) * scale)
            p.rotate(angle)
            p.scale(scale, scale)
            box = QRectF(-32, -50, 64, 100)
            foil = QLinearGradient(box.topLeft(), box.bottomRight())
            foil.setColorAt(0, QColor(t.RAISED))
            foil.setColorAt(0.54, QColor(t.RAIL))
            foil.setColorAt(1, QColor(t.HERO_DARK))
            p.setBrush(foil)
            p.setPen(QPen(QColor(accent), 1.2))
            p.drawRoundedRect(box, 5, 5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(t.BRONZE), 0.8))
            p.drawRoundedRect(box.adjusted(5, 5, -5, -5), 4, 4)
            p.drawEllipse(QRectF(-22, -22, 44, 44))
            icon(symbol, accent).paint(p, -16, -16, 32, 32)
            for top in (-38, 30):
                p.save()
                p.translate(0, top + 4)
                p.rotate(45)
                p.drawRect(QRectF(-2, -2, 4, 4))
                p.restore()
            p.restore()


class Screen(QWidget):
    def __init__(self, title: str = "", back: Callable[[], object] | None = None) -> None:
        super().__init__()
        self.body = QVBoxLayout(self)
        self.setMaximumWidth(t.CONTENT_MAX)
        self.body.setContentsMargins(t.XXL, t.XL, t.XXL, t.XL)
        self.body.setSpacing(t.MD)
        self.initial: QWidget | None = None
        if back:
            row = QHBoxLayout()
            self.back = button("Back", back, "quiet", "back")
            row.addWidget(self.back)
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


class Contents(DeckContents):
    def __init__(self, runner: TaskRunner, game: Path, preview: Preview) -> None:
        super().__init__(runner)
        job = runner.start(lambda worker: saved_sections(game, preview, worker.cancel))
        job.result.connect(self.set_sections)

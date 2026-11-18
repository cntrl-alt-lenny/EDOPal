"""Practice library rows and the shared inline reading surfaces."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListView,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.pages.decks_contents import paint_text
from studio.widgets import Panel, label, margins


def across(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(t.SM)
    for widget in widgets:
        row.addWidget(widget)
    return row


def section(panel: Panel, title: str, text: str) -> None:
    panel.body.addWidget(label(title.upper(), "goldKicker"))
    panel.body.addWidget(label(text, "body", True))


def scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def column() -> tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    margins(layout, 0)
    layout.setSpacing(t.MD)
    return widget, layout


class Rows(QAbstractListModel):
    def __init__(self, parent: QListView) -> None:
        super().__init__(parent)
        self.values: list[tuple[str, str, str]] = []

    def replace(self, values: list[tuple[str, str, str]]) -> None:
        self.beginResetModel()
        self.values = values
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.values)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if index.isValid():
            row = self.values[index.row()]
            if role == Qt.ItemDataRole.UserRole:
                return row
            if role in (
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.ToolTipRole,
                Qt.ItemDataRole.AccessibleTextRole,
            ):
                return " · ".join(row[:2])
        return None


class RowPainter(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(1, t.HUGE + t.MD)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        name, detail, colour = index.data(Qt.ItemDataRole.UserRole)
        painter.save()
        rect = option.rect.adjusted(0, t.HAIR, 0, -t.HAIR)
        active = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(t.ACTIVE if active else t.PANEL))
        painter.setPen(
            QPen(QColor(t.PALE), t.FOCUS)
            if option.state & QStyle.StateFlag.State_HasFocus
            else Qt.PenStyle.NoPen
        )
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), t.R_SMALL, t.R_SMALL)
        x = rect.left() + t.LG
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QRect(x, rect.center().y() - t.XS, t.SM, t.SM))
        copy = rect.adjusted(t.XXL, t.XS, -t.MD, -t.XS)
        paint_text(
            painter, copy.adjusted(0, 0, 0, -t.XL), name, t.PALE if active else t.INK, t.BODY, True
        )
        paint_text(painter, copy.adjusted(0, t.XL - t.XS, 0, 0), detail, t.MUTED, t.SMALL)
        painter.restore()


class PracticeList(QListView):
    def __init__(self) -> None:
        super().__init__()
        self.rows = Rows(self)
        self.setModel(self.rows)
        self.setItemDelegate(RowPainter(self))
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setAccessibleName("Practice results")
        self.setStyleSheet(f"QListView {{ background: {t.RAIL}; padding: {t.XS}px; }}")

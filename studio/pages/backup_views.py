"""Inline panels and virtualized review lists for Backup & transfer."""

from collections.abc import Callable, Sequence
from functools import partial
from html import escape

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListView,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

import backup
from studio import theme as t
from studio.icons import icon
from studio.pages.backup_data import CATEGORIES, Row, amount, undo_blocked_text
from studio.widgets import Button, ElidedLabel, Panel, glyph, label, margins


def serif(text: str, size: int = t.DISPLAY) -> QLabel:
    title = label(text, "display", True)
    title.setStyleSheet(f'font-family: "{t.SERIF}", serif; font-size: {size}px;')
    return title


def action(
    text: str, callback: Callable[[], None], role: str = "secondary", symbol: str = ""
) -> Button:
    button = Button(text, role, symbol)
    button.clicked.connect(lambda: callback())
    return button


def actions(*buttons: Button, description: str = "") -> Panel:
    panel = Panel(t.LG)
    row = QHBoxLayout()
    row.setSpacing(t.MD)
    row.addWidget(label(description, "body", True), 1)
    for button in buttons:
        row.addWidget(button)
    panel.body.addLayout(row)
    return panel


class UndoStatus(QWidget):
    def __init__(self, text: str = "") -> None:
        super().__init__()
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.XS)
        self.message = label(text, "body", True)
        self.path = path_label("")
        body.addWidget(self.message)
        body.addWidget(self.path)
        self.path.hide()

    def set_message(self, text: str) -> None:
        self.message.setText(text)
        self.path.hide()

    def show_preview(self, preview: backup.UndoPreview, help_text: str) -> None:
        self.set_message(help_text if preview.available else undo_blocked_text(preview))
        if preview.target:
            self.path.set_value(preview.target)
            self.path.setToolTip(text_tooltip(preview.target))
            self.path.show()


def text_tooltip(value: str) -> str:
    return "<p>" + "<br>".join(escape(line) for line in value.splitlines()) + "</p>"


def path_label(path: str, display: str | None = None) -> ElidedLabel:
    item = ElidedLabel()
    item.set_value(path, display)
    item.setToolTip(text_tooltip(path))
    return item


def information(symbol: str, headline: str, description: str) -> Panel:
    panel = Panel()
    title = QHBoxLayout()
    title.setSpacing(t.MD)
    title.addWidget(glyph(symbol, t.ICON_BIG))
    title.addWidget(serif(headline, t.HEADING + t.XS), 1)
    panel.body.addLayout(title)
    panel.body.addWidget(label(description, "body", True))
    return panel


class ArrowFocus(QObject):
    def __init__(self, parent: QWidget, buttons: Sequence[Button], columns: int) -> None:
        super().__init__(parent)
        self.buttons, self.columns = buttons, columns
        for button in buttons:
            button.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            offsets = {
                Qt.Key.Key_Left: (0, -1),
                Qt.Key.Key_Right: (0, 1),
                Qt.Key.Key_Up: (-1, 0),
                Qt.Key.Key_Down: (1, 0),
            }
            if direction := offsets.get(event.key()):
                row, column = divmod(self.buttons.index(watched), self.columns)
                row, column = row + direction[0], column + direction[1]
                index = row * self.columns + column
                if row >= 0 and 0 <= column < self.columns and index < len(self.buttons):
                    target = self.buttons[index]
                    if target.isEnabled():
                        target.setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)


class CategoryChoice(Button):
    def __init__(self, category: str, total: backup.Total) -> None:
        title, hint, symbol = CATEGORIES[category]
        summary = amount(total)
        super().__init__("", "scope")
        self.setAccessibleName(title)
        self.setAccessibleDescription(f"{hint} {summary}")
        self.setCheckable(True)
        row = QHBoxLayout(self)
        margins(row, t.MD)
        row.setSpacing(t.MD)
        row.addWidget(glyph(symbol, t.ICON_BIG))
        copy = QWidget()
        stack = QVBoxLayout(copy)
        margins(stack, 0)
        stack.setSpacing(t.XS)
        stack.addWidget(label(title, "subheading"))
        stack.addWidget(label(hint, "muted", True))
        row.addWidget(copy, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label(summary, "small"))
        self.marker = QLabel()
        self.marker.setFixedSize(t.LG, t.LG)
        row.addWidget(self.marker)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toggled.connect(self._mark)
        self._mark(False)

    def _mark(self, checked: bool) -> None:
        fill, edge = (t.GOLD, t.GOLD) if checked else (t.CLEAR, t.MUTED)
        self.marker.setStyleSheet(
            f"background: {fill}; border: {t.HAIR}px solid {edge}; border-radius: {t.XS}px;"
        )
        if checked:
            self.marker.setPixmap(icon("check", t.ON_GOLD).pixmap(t.LG, t.LG))
        else:
            self.marker.clear()


class Rows(QAbstractListModel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.rows: Sequence[Row] = ()

    def replace(self, rows: Sequence[Row]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent and parent.isValid() else len(self.rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return text_tooltip(row.tooltip)
        if role == Qt.ItemDataRole.AccessibleDescriptionRole:
            return row.tooltip
        if role == Qt.ItemDataRole.UserRole:
            return row
        return None


class RowDelegate(QStyledItemDelegate):
    def __init__(self, parent: QListView, show_action: bool) -> None:
        super().__init__(parent)
        self.show_action = show_action
        self.compact = False
        self.expanded = QPersistentModelIndex()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        height = t.TILE_H - t.SM if self.show_action else t.BUTTON_H + t.MD
        if self.compact and self.show_action:
            height = t.HUGE - t.XS
        if index == self.expanded:
            height += t.LG * len(self._detail_lines(index.data(Qt.ItemDataRole.UserRole)))
        return QSize(0, height)

    def _detail_lines(self, row: Row) -> list[str]:
        names = row.names.splitlines()
        return ["  ·  ".join(names)] if self.compact and names else names

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(Qt.ItemDataRole.UserRole)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        if selected:
            painter.fillRect(rect, QColor(t.ACTIVE))
        painter.setPen(QColor(t.EDGE))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        top = rect.y() + (t.XS if self.compact else t.SM)
        first, rest = (t.LG, t.MD) if self.compact else (t.XL - t.XS, t.LG + t.XS)
        lines = [
            (row.title, t.PALE if selected else t.INK, t.BODY, first),
            (row.detail, t.TEXT, t.SMALL, rest),
            (row.action, t.GOLD, t.SMALL, rest),
        ]
        if index == self.expanded:
            top_details = [(name, t.MUTED, t.SMALL, t.LG) for name in self._detail_lines(row)]
            lines.extend(top_details)
        for value, colour, size, height in lines:
            if not value:
                continue
            painter.setFont(t.font(size, bold=value == row.title))
            painter.setPen(QColor(colour))
            line = QRect(rect.x() + t.MD, top, rect.width() - 2 * t.MD, height)
            text = painter.fontMetrics().elidedText(
                value, Qt.TextElideMode.ElideMiddle, line.width()
            )
            painter.drawText(line, Qt.AlignmentFlag.AlignVCenter, text)
            top += height
        painter.restore()


class ReviewList(QListView):
    def __init__(self, name: str, *, show_action: bool = False) -> None:
        super().__init__()
        self.setAccessibleName(name)
        self.rows = Rows(self)
        self.setModel(self.rows)
        self.setItemDelegate(RowDelegate(self, show_action))
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QListView {{ padding: 0; background: {t.CANVAS}; }}")

    def replace(self, rows: Sequence[Row]) -> None:
        self.rows.replace(rows)
        self.scrollToTop()


class GroupedList(Panel):
    def __init__(self, headings: dict[str, str], messages: dict[str, str]) -> None:
        super().__init__(t.LG)
        self.messages = messages
        self.headings = headings
        self.groups: dict[str, list[Row]] = {}
        self.tabs: dict[str, Button] = {}
        show_action = "add" in headings
        group = QButtonGroup(self)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(t.SM)
        for key, title in headings.items():
            tab = Button(title, "scope")
            tab.setCheckable(True)
            tab.setStyleSheet(f"padding: 0 {t.MD}px;")
            group.addButton(tab)
            tab.toggled.connect(partial(self._select, key))
            self.tabs[key] = tab
            toolbar.addWidget(tab)
        toolbar.addStretch()
        self.details_button = Button("Item details", "quiet")
        self.details_button.setCheckable(True)
        if show_action:
            toolbar.addWidget(self.details_button)
        self.body.addLayout(toolbar)
        self.list = ReviewList("File review", show_action=show_action)
        self.list.setMinimumHeight(t.TILE_H * 2)
        self.body.addWidget(self.list, 1)
        self.empty = label("", "body", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(t.TILE_H * 2)
        self.body.addWidget(self.empty, 1)
        self.details_button.toggled.connect(self._expand_details)
        self.list.selectionModel().currentChanged.connect(self._item_changed)
        if not show_action:
            self.body.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignRight)

    def _item_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        item = current.data(Qt.ItemDataRole.UserRole)
        self.details_button.setEnabled(bool(item and item.names))
        self._expand_details(self.details_button.isChecked())

    def _expand_details(self, opened: bool) -> None:
        current = self.list.currentIndex()
        delegate = self.list.itemDelegate()
        expanded = QPersistentModelIndex(current) if opened else QPersistentModelIndex()
        if expanded == delegate.expanded:
            return
        self.list.setUniformItemSizes(not opened)
        delegate.expanded = expanded
        self.list.doItemsLayout()
        if opened:
            self.list.scrollTo(current)
        self.list.viewport().update()

    def close_details(self) -> bool:
        if not self.details_button.isChecked():
            return False
        self.details_button.setChecked(False)
        self.details_button.setFocus()
        return True

    def compact(self, small: bool) -> None:
        delegate = self.list.itemDelegate()
        if delegate.compact != small:
            delegate.compact = small
            self.list.doItemsLayout()
            self.list.viewport().update()
        height = (t.HUGE - t.XS) * 3 + 2 * t.HAIR if small and delegate.show_action else t.TILE_H
        margins(self.body, t.SM if small else t.LG)
        self.body.setSpacing(t.SM if small else t.MD)
        self.list.setMinimumHeight(height)
        self.empty.setMinimumHeight(t.TILE_H if small else t.TILE_H * 2)

    def set_groups(self, groups: dict[str, list[Row]], preferred: str | None = None) -> None:
        self.groups = groups
        first = preferred or next(
            (key for key in self.headings if groups[key]), next(iter(self.headings))
        )
        for key, title in self.headings.items():
            self.tabs[key].setText(f"{title}  {len(groups[key]):,}")
        self.tabs[first].setChecked(True)
        self._select(first, True)

    def _select(self, key: str, checked: bool) -> None:
        if checked:
            rows = self.groups[key]
            self.details_button.setChecked(False)
            self.empty.setText(self.messages[key])
            self.empty.setVisible(not rows)
            self.list.setVisible(bool(rows))
            self.list.setAccessibleName(self.headings[key])
            self.list.replace(rows)
            self.list.setCurrentIndex(self.list.rows.index(0, 0))
            self.details_button.setVisible(bool(rows))

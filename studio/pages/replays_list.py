from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from replays import ReplayEntry
from studio import theme as t
from studio.icons import icon
from studio.pages.replays_library import details_line, played, players, title
from studio.widgets import ActivateOnEnter


class ReplayModel(QAbstractListModel):
    entries: tuple[ReplayEntry, ...] = ()

    def replace(self, entries: tuple[ReplayEntry, ...]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.entries)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> str | None:
        if index.isValid() and role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.AccessibleTextRole,
        ):
            entry = self.entries[index.row()]
            return "\n".join(
                (
                    title(entry),
                    players(entry),
                    details_line(entry),
                    played(entry),
                    ", ".join(entry.annotation.tags),
                )
            )


class ReplayRow(QStyledItemDelegate):
    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(0, t.TILE_H)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        entry = index.model().entries[index.row()]
        rect = option.rect
        painter.save()
        active = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect, QColor(t.ACTIVE if active else t.PANEL))
        painter.setPen(QColor(t.EDGE))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(t.PALE), t.FOCUS))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), t.R_SMALL, t.R_SMALL)
        badge = QRect(rect.x() + t.MD, rect.center().y() - t.ICON, t.BUTTON_H, t.BUTTON_H)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.RAISED))
        painter.drawRoundedRect(badge, t.R_SMALL, t.R_SMALL)
        icon("warning" if entry.error else "replays", t.RED if entry.error else t.GOLD).paint(
            painter, badge.adjusted(t.SM, t.SM, -t.SM, -t.SM)
        )
        left = badge.right() + t.LG
        right = rect.right() - t.MD
        meta_width = min(t.RAIL_WIDTH, rect.width() // 3)

        def draw(
            text: str, x: int, y: int, width: int, size: int, colour: str, *, end: bool = False
        ) -> None:
            painter.setFont(t.font(size, bold=size == t.SUBTITLE))
            painter.setPen(QColor(colour))
            area = QRect(x, y, width, t.XL)
            painter.drawText(
                area,
                (Qt.AlignmentFlag.AlignRight if end else Qt.AlignmentFlag.AlignLeft)
                | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width),
            )

        width = right - meta_width - left - t.MD
        name = title(entry)
        detail = players(entry)
        tags = "   ·   ".join(entry.annotation.tags)
        draw(name, left, rect.top() + t.SM, width, t.SUBTITLE, t.INK)
        draw(
            detail if detail != name else "Saved copy",
            left,
            rect.top() + t.XXL,
            width,
            t.BODY,
            t.TEXT,
        )
        draw(
            tags or ("Last duel recording" if entry.is_last else "Saved replay"),
            left,
            rect.top() + t.XXL + t.ICON,
            width,
            t.SMALL,
            t.GOLD if tags else t.MUTED,
        )
        draw(
            played(entry),
            right - meta_width,
            rect.top() + t.LG,
            meta_width,
            t.BODY,
            t.TEXT,
            end=True,
        )
        draw(
            details_line(entry),
            right - meta_width,
            rect.top() + t.BUTTON_H,
            meta_width,
            t.SMALL,
            t.RED if entry.error else t.MUTED,
            end=True,
        )
        painter.restore()


class ReplayList(QListView):
    def __init__(self) -> None:
        super().__init__()
        ActivateOnEnter(self)
        self.setAccessibleName("Replay library")
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            f"QListView {{background:{t.PANEL}; padding:0; border: {t.HAIR}px solid {t.EDGE}; border-radius:{t.R_SMALL}px;}} QListView:focus {{border-color:{t.PALE};}}"
        )
        self.records = ReplayModel(self)
        self.setModel(self.records)
        self.setItemDelegate(ReplayRow(self))

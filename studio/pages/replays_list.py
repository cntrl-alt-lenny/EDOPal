from collections.abc import Callable

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
)

from replays import ReplayEntry
from studio import theme as t
from studio.icons import icon
from studio.pages.health_layout import row
from studio.pages.replays_library import SORTS, details_line, played, players, title
from studio.widgets import Button, Panel, SearchField, SectionHeader, glyph, label


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


class ReplayShelf(Panel):
    def __init__(
        self, query: str, order: str, populated: bool, refresh: Callable[[], None]
    ) -> None:
        super().__init__(t.LG)
        self.count = label("", "muted")
        self.reload = Button("Refresh", "quiet" if populated else "secondary")
        self.reload.clicked.connect(refresh)
        if not populated:
            self.body.addWidget(glyph("replays", t.BRAND_ICON))
            self.body.addWidget(
                SectionHeader(
                    "Your first replay goes here",
                    "Finish a duel in EDOPro and save the replay. Refresh when you return to see it here.",
                )
            )
            self.body.addWidget(self.reload, 0, Qt.AlignmentFlag.AlignLeft)
            return
        controls = row()
        self.search = SearchField("Search replays")
        self.search.setToolTip("Search player names, labels, notes and tags.")
        self.search.setText(query)
        controls.addWidget(self.search, 1)
        controls.addWidget(label("Sort by", "muted"))
        self.sort = QComboBox()
        self.sort.setAccessibleName("Sort replays")
        self.sort.addItems(list(SORTS))
        self.sort.setCurrentText(order)
        self.sort.setStyleSheet(f"""QComboBox {{background:{t.RAISED}; border:{t.HAIR}px solid {t.EDGE};
            border-radius:{t.R_SMALL}px; padding:{t.SM}px {t.MD}px; min-width:{t.TILE_H}px;}}
            QComboBox:focus {{border-color:{t.PALE};}}""")
        controls.addWidget(self.sort)
        controls.addWidget(self.count)
        controls.addWidget(self.reload)
        self.body.addLayout(controls)
        self.list = ReplayList()
        self.list.setMinimumHeight(t.TILE_H)
        self.body.addWidget(self.list, 1)
        self.no_matches = Panel()
        self.no_matches.body.addWidget(
            SectionHeader("No matching duels", "Try another player, label or tag.")
        )
        clear = Button("Clear search")
        clear.clicked.connect(self.search.clear)
        self.no_matches.body.addWidget(clear, 0, Qt.AlignmentFlag.AlignLeft)
        self.no_matches.body.addStretch()
        self.body.addWidget(self.no_matches, 1)


def last_duel_notice(date: str, save: Callable[[], None]) -> tuple[Panel, Button]:
    panel = Panel(t.LG)
    line = row()
    line.addWidget(glyph("replays", t.ICON_BIG))
    copy = QVBoxLayout()
    copy.setSpacing(t.XS)
    copy.addWidget(label("Your last duel is ready to save", "subheading", True))
    copy.addWidget(label("Save a copy before EDOPro replaces it in your next duel.", "body", True))
    line.addLayout(copy, 1)
    button = Button("Save my last duel", "primary", "download")
    button.setToolTip(date)
    button.clicked.connect(save)
    line.addWidget(button)
    panel.body.addLayout(line)
    return panel, button


def saved_copy_notice(reveal: Callable[[], None], remove: Callable[[], None] | None) -> Panel:
    panel = Panel(t.MD)
    line = row()
    line.addWidget(glyph("check", colour=t.GREEN))
    line.addWidget(
        label("Replay saved. You can watch it the next time you start EDOPro.", "body", True), 1
    )
    show_file = Button("Show saved copy", "secondary", "folder")
    show_file.clicked.connect(reveal)
    line.addWidget(show_file)
    if remove:
        remove_copy = Button("Remove saved copy", "secondary")
        remove_copy.clicked.connect(remove)
        line.addWidget(remove_copy)
    panel.body.addLayout(line)
    return panel

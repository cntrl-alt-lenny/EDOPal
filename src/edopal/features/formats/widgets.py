"""Qt widgets for the historical format picker and tournament browser."""

from __future__ import annotations

import weakref
from datetime import date
from functools import partial
from math import cos, pi, sin
from zlib import crc32

from PySide6.QtCore import QAbstractListModel, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListView,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from edopal.ui import theme as t

# Each format has a foil colour and its own engraved rosette.
FOILS = {
    "goat": ("backup", 6),
    "edison": ("replays", 8),
    "hat": ("decks", 4),
    "tengu": ("health", 5),
    "wind-up": ("music", 7),
    "cat": ("sleeves", 5),
    "trooper": ("skins", 6),
    "chaos": ("brand", 8),
}


# Format Library's event anchors for the eight TCG snapshots.
ANCHORS = {
    "goat": "SJC Indianapolis",
    "edison": "SJC Edison",
    "hat": "North American WCQ",
    "tengu": "YCS Toronto",
    "wind-up": "YCS Providence",
    "cat": "SJC Indianapolis",
    "trooper": "SJC Indianapolis",
    "chaos": "U.S. National Championship",
}


def _foil(format_id):
    if format_id in FOILS:
        return FOILS[format_id]
    seed = crc32(format_id.encode("utf-8"))
    colours = tuple(FOILS.values())
    return colours[seed % len(colours)][0], 3 + (seed // len(colours)) % 6


def pretty_date(value, fmt="%d %b %Y"):
    try:
        return date.fromisoformat(value[:10]).strftime(fmt).lstrip("0")
    except (TypeError, ValueError):
        return value or "—"


def validation_summary(data):
    decks = data.get("decks", 0)
    if not decks:
        return "No samples"
    passed, failed = data.get("passed_decks"), data.get("failed_decks")
    if failed is None and data.get("failures") == 0:
        passed, failed = decks, 0
    if passed is not None and failed is not None:
        return f"{passed} passed · {failed} failed"
    return f"{decks} sampled"


def label(text="", role="body"):
    widget = QLabel(str(text))
    widget.setProperty("role", role)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text, role, callback, name):
    widget = QPushButton(text)
    widget.setProperty("role", role)
    widget.setObjectName(name)
    if role in ("primary", ""):
        widget.setMinimumHeight(t.BUTTON_H)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if isinstance(callback, partial):
        # Native signal connections keep partials alive. Avoid owning the enclosing
        # page (or deck stack) from one of its child buttons during destruction.
        target_ref = weakref.ref(callback.func.__self__)
        name, args = callback.func.__name__, callback.args

        def clicked():
            target = target_ref()
            if target is not None and isValid(target):
                getattr(target, name)(*args)

        widget.clicked.connect(clicked)
    else:
        widget.clicked.connect(callback)
    return widget


def _tint(colour, alpha):
    result = QColor(colour)
    result.setAlpha(alpha)
    return result


def _sleeve(painter, format_id, *, small=False):
    """Draw in a shared 68×96 coordinate system with the origin at the card centre."""
    foil, petals = _foil(format_id)
    low, high = t.FOIL[foil]
    grad = QLinearGradient(-30, -44, 28, 46)
    for pos, colour in ((0, high), (0.47, low), (0.84, high), (1, low)):
        grad.setColorAt(pos, QColor(colour))
    painter.setBrush(QBrush(grad))
    painter.setPen(QPen(_tint(t.GOLD, 154), 1.1))
    painter.drawRoundedRect(QRectF(-33, -47, 66, 94), 3, 3)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_tint(t.GOLD, 150), 1.5 if small else 0.6))
    painter.drawRect(QRectF(-28, -41, 56, 82))
    if not small:
        painter.drawRect(QRectF(-26.5, -39.5, 53, 79))
        for x, y in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            painter.save()
            painter.scale(x, y)
            for d in (0, 2, 4):
                painter.drawLine(QPointF(12 + d, 38), QPointF(25, 25 + d))
            painter.drawEllipse(QPointF(21.5, 34.5), 1.4, 1.4)
            painter.restore()
    painter.setPen(QPen(_tint(t.PALE, 190), 1.3 if small else 0.65))
    painter.drawEllipse(QPointF(0, 0), 21, 21)
    painter.drawEllipse(QPointF(0, 0), 17.5, 17.5)
    for i in range(petals):
        painter.save()
        painter.rotate(i * 360 / petals)
        petal = QPainterPath(QPointF(0, -3))
        petal.quadTo(-8.3, -12.5, 0, -24.5)
        petal.quadTo(7.6, -13, 0, -3)
        painter.drawPath(petal)
        painter.restore()
    points = [QPointF(6.6 * sin(i * pi / 4), 6.6 * cos(i * pi / 4)) for i in range(8)]
    painter.setBrush(QColor(low))
    painter.drawPolygon(QPolygonF(points))
    painter.setBrush(_tint(t.GOLD, 200))
    painter.drawPolygon(QPolygonF([QPointF(0, -4), QPointF(2.6, 0), QPointF(0, 4), QPointF(-2.6, 0)]))


ID_ROLE = Qt.ItemDataRole.UserRole
META_ROLE = int(ID_ROLE) + 1


class _ProfileDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(1, 1, -2, -1)
        slug = index.data(ID_ROLE)
        title = index.data(Qt.ItemDataRole.DisplayRole)
        if slug is None:
            painter.setFont(t.font(10, bold=True))
            painter.setPen(QColor(t.GOLD))
            painter.drawText(rect.adjusted(5, 4, 0, 0), Qt.AlignmentFlag.AlignVCenter, title.upper())
            painter.restore()
            return
        selected = option.state & QStyle.StateFlag.State_Selected
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            painter.setBrush(QColor(t.ACTIVE if selected else t.RAISED))
            painter.setPen(QPen(QColor(t.BRONZE), 1) if selected else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 6, 6)
        painter.save()
        painter.translate(rect.left() + 19, rect.center().y() + 0.5)
        painter.rotate(-7)
        painter.scale(0.30, 0.30)
        _sleeve(painter, slug, small=True)
        painter.restore()
        x, width = rect.left() + 38, max(4, rect.width() - 43)
        painter.setFont(t.font(14, bold=True))
        painter.setPen(QColor(t.INK))
        painter.drawText(x, rect.top() + 17, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, width))
        painter.setFont(t.font(11))
        painter.setPen(QColor(t.TEXT if selected else t.MUTED))
        painter.drawText(x, rect.top() + 32, index.data(META_ROLE))
        painter.restore()


class _CatalogModel(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.entries = []

    def set_rows(self, rows):
        entries = []
        previous = None
        for group, profile in rows:
            if group != previous:
                entries.append((group, None, ""))
                previous = group
            date_label = pretty_date(profile["date"]) if profile.get("date") else "Rolling"
            entries.append((profile["name"], profile["id"], f"{profile['region']}  ·  {date_label}"))
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        title, slug, meta = self.entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return title
        if role == ID_ROLE:
            return slug
        if role == META_ROLE:
            return meta
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(0, 41 if slug else 24)
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{title}, {meta}" if slug else title
        return None

    def flags(self, index):
        if not index.data(ID_ROLE):
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


class ProfilePicker(QListView):
    def __init__(self):
        super().__init__()
        self.setObjectName("formatPicker")
        self.setAccessibleName("Format catalog")
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setModel(_CatalogModel(self))
        self.setItemDelegate(_ProfileDelegate(self))

    def populate(self, rows, current):
        model = self.model()
        model.set_rows(rows)
        first = None
        for i, (_, slug, _) in enumerate(model.entries):
            if slug and first is None:
                first = i
            if slug == current:
                first = i
                break
        if first is not None:
            index = model.index(first, 0)
            self.setCurrentIndex(index)
            self.scrollTo(index)
            return index.data(ID_ROLE)
        return None


class Hero(QFrame):
    def __init__(self):
        super().__init__()
        self.profile = None
        self.setMinimumHeight(162)
        self.setMaximumHeight(220)
        self.setObjectName("formatHero")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(t.XL, t.MD, t.LG, t.MD)
        layout.setSpacing(0)
        self.era = label("", "goldKicker")
        self.title = label("Formats", "heroTitle")
        self.title.setStyleSheet(f"font-size: {t.PAGE_TITLE}px;")
        self.title.setObjectName("selectedFormatTitle")
        layout.addWidget(self.era)
        layout.addWidget(self.title)
        self.description = label("", "body")
        layout.addWidget(self.description)
        layout.addStretch()
        self.meta = QHBoxLayout()
        self.meta.setSpacing(26)
        self.values = []
        self.headings = []
        for heading in ("SNAPSHOT", "BANLIST", "CARD POOL", "DECK CHECKS"):
            col = QVBoxLayout()
            col.setSpacing(3)
            title = label(heading, "kicker")
            self.headings.append(title)
            col.addWidget(title)
            value = label("—", "profileValue")
            col.addWidget(value)
            self.values.append(value)
            self.meta.addLayout(col)
        self.meta.addStretch()
        layout.addLayout(self.meta)

    def set_profile(self, profile):
        self.profile = profile
        self.title.setText(profile["name"])
        region = profile.get("region") or profile.get("category") or "TCG"
        self.era.setText("   /   ".join(x.upper() for x in (region, profile.get("era", "")) if x))
        event_name = profile.get("event") or ANCHORS.get(profile["id"], "")
        self.description.setText("Hand, Artifact & Traptrix · North American WCQ" if profile["id"] == "hat" else event_name)
        self.description.setVisible(bool(self.description.text()))
        self.values[0].setText(pretty_date(profile["date"]) if profile.get("date") else "Rolling")
        self.values[1].setText(pretty_date(profile["banlist_date"]) if profile.get("banlist_date") else profile["banlist"])
        count = profile.get("pool")
        historical = profile.get("status", "historical") == "historical"
        self.headings[2].setVisible(historical)
        self.values[2].setVisible(historical)
        self.values[2].setText(f"{count:,} cards" if isinstance(count, int) else "Unmeasured")
        self.headings[3].setVisible(historical)
        self.values[3].setVisible(historical)
        self.values[3].setText(validation_summary(profile.get("validation", {})))
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(frame, t.R_PANEL, t.R_PANEL)
        painter.setClipPath(clip)
        format_id = self.profile["id"] if self.profile else "hat"
        foil, _ = _foil(format_id)
        low, high = t.FOIL[foil]
        gradient = QLinearGradient(0, 0, frame.width(), frame.height() * 0.6)
        for pos, colour in ((0, t.PANEL), (0.4, low), (1, high)):
            gradient.setColorAt(pos, QColor(colour))
        painter.fillPath(clip, gradient)
        w, h = frame.width(), frame.height()
        # A set of etched concentric arcs sits behind the full-size foil cards.
        painter.setPen(QPen(_tint(t.GOLD, 28), 0.8))
        center = QPointF(w - 32, 0.75 * h)
        for radius in (0.56, 0.62, 0.68, 1.16, 1.2, 1.42):
            painter.drawEllipse(center, h * radius, h * radius)
        compact = w < 780
        x = w - (39 if compact else 106)
        scale = h / 101
        for dx, dy, angle in ((-55, -5, -24), (-17, 11, -6), (29, 26, 14)):
            painter.save()
            painter.translate(x + dx * scale, h * 0.48 + dy * scale)
            painter.rotate(angle)
            painter.scale(scale, scale)
            _sleeve(painter, format_id)
            painter.restore()
        # Fade the print under the text on narrow windows.
        shade = QLinearGradient(0, 0, w, 0)
        for pos, alpha in ((0, 242), (min(0.69, 450 / max(w, 1)), 238), (0.84, 0)):
            shade.setColorAt(pos, _tint(low, alpha))
        painter.fillPath(clip, shade)
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_tint(t.GOLD, 62), 1))
        painter.drawPath(clip)


class DeckThumbnail(QWidget):
    """Display the FL image proportionally as the surrounding window changes size."""

    def __init__(self, pixmap):
        super().__init__()
        self.pixmap = pixmap
        self.setMaximumWidth(430)
        self.setAccessibleName("Main deck thumbnail from Format Library")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        painter.drawPixmap(0, 0, size.width(), size.height(), self.pixmap)


class BrowserTable(QTableWidget):
    opened = Signal(int)

    def __init__(self, headings, fixed):
        super().__init__(0, len(headings))
        self.event_columns = headings[0] == "EVENT"
        self.setObjectName("tournamentRows")
        self.setColumnCount(len(headings))
        self.setHorizontalHeaderLabels(headings)
        self.setShowGrid(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWordWrap(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(42)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.horizontalHeader().setMinimumSectionSize(22)
        self.horizontalHeader().setSectionsClickable(False)
        for i in range(len(headings)):
            if i in fixed:
                self.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self.setColumnWidth(i, fixed[i])
            else:
                self.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.cellClicked.connect(self._open_cell)

    def _open_cell(self, row, _column):
        self.opened.emit(row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.event_columns:
            width = self.viewport().width()
            self.setColumnWidth(1, max(98, round(width * 0.23)))
            self.setColumnWidth(2, max(65, round(width * 0.14)))

    def fill(self, rows):
        self.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value) if value != "›" else "")
                if col == len(values) - 1:
                    item.setForeground(QColor(t.GOLD))
                self.setItem(row, col, item)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentRow() >= 0:
            self.opened.emit(self.currentRow())
            return
        super().keyPressEvent(event)


STYLE = f"""
    QListView#formatPicker {{ background: {t.CANVAS}; border: 1px solid transparent; border-radius: 6px; padding: 0; }}
    QListView#formatPicker[keyboardFocus=true]:focus {{ border-color: {t.PALE}; }}
    QLineEdit#formatSearch {{ padding: 7px 8px; background: {t.PANEL}; }}
    QPushButton[role="region"] {{ background: {t.PANEL}; color: {t.MUTED}; padding: 4px 2px; border: 1px solid {t.EDGE}; }}
    QPushButton[role="region"]:checked {{ background: {t.ACTIVE}; color: {t.GOLD}; border-color: {t.BRONZE}; }}
    QPushButton[role="region"][keyboardFocus=true]:focus {{ border-color: {t.PALE}; }}
    QLabel[role="profileValue"] {{ color: {t.INK}; font-size: 15px; font-weight: 650; }}
    QPushButton[role="deckSection"] {{ background: transparent; border: 0; border-bottom: 2px solid {t.EDGE};
        padding: 5px 8px; text-align: left; color: {t.MUTED}; border-radius: 0; font-size: 12px; }}
    QPushButton[role="deckSection"]:checked {{ color: {t.GOLD}; border-bottom-color: {t.GOLD}; }}
    QLabel[role="quantity"] {{ color: {t.GOLD}; font-size: 13px; }}
    QLabel[role="ok"] {{ color: {t.GREEN}; font-size: 13px; }}
    QLabel[role="warning"] {{ color: {t.RED}; font-size: 13px; }}
    QPushButton[role="source"], QPushButton[role="back"] {{ background: transparent; color: {t.GOLD};
        padding: 4px; border: 0px; font-size: 12px; text-align: left; }}
    QPushButton[role="source"]:hover, QPushButton[role="back"]:hover {{ color: {t.PALE}; }}
    QPushButton[role="sectionTab"] {{ border: 1px solid transparent; border-radius: 6px; padding: 6px 12px;
        color: {t.MUTED}; background: transparent; }}
    QPushButton[role="sectionTab"]:checked {{ background: {t.RAISED}; color: {t.INK}; border-color: {t.EDGE}; }}
    QPushButton[role="sectionTab"]:hover {{ color: {t.INK}; }}
    QTableWidget#tournamentRows {{ background: {t.PANEL}; color: {t.TEXT}; border: 0; padding: 0;
        selection-color: {t.PALE}; selection-background-color: {t.ACTIVE}; outline: none; }}
    QTableWidget#tournamentRows::item {{ border-bottom: 1px solid {t.EDGE}; padding: 0 8px; }}
    QTableWidget#tournamentRows::item:hover {{ background: {t.HOVER}; }}
    QHeaderView {{ background: {t.PANEL}; }}
    QHeaderView::section {{ background: {t.PANEL}; color: {t.MUTED}; font-size: 11px; letter-spacing: 1.1px;
        border: 0; border-bottom: 1px solid {t.EDGE}; padding: 4px 8px 8px 8px; }}
    QScrollArea#readArea, QScrollArea#readArea > QWidget > QWidget {{ background: {t.PANEL}; }}
"""

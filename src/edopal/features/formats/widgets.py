"""Responsive Qt surfaces for format profiles and tournament lists."""

from __future__ import annotations

import weakref
from datetime import date
from functools import partial

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from edopal.ui import theme as t


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
    elif callback is not None:
        widget.clicked.connect(callback)
    return widget


class BrowserTable(QTableWidget):
    opened = Signal(int)

    def __init__(self, headings, fixed):
        super().__init__(0, len(headings))
        self.section = headings[0]
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
        if self.section == "Event":
            width = self.viewport().width()
            self.setColumnWidth(1, max(98, round(width * 0.23)))
            self.setColumnWidth(2, max(65, round(width * 0.14)))
        elif self.section == "Format":
            width = self.viewport().width()
            self.setColumnWidth(1, max(113, round(width * 0.22)))
            self.setColumnWidth(2, max(58, round(width * 0.16)))
            self.setColumnWidth(3, max(64, round(width * 0.14)))

    def fill(self, rows):
        self.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value) if value != "›" else "")
                if value == "›":
                    item.setForeground(QColor(t.GOLD))
                self.setItem(row, col, item)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentRow() >= 0:
            self.opened.emit(self.currentRow())
            return
        super().keyPressEvent(event)


def layout(parent, kind=QVBoxLayout, margin=0, spacing=8):
    result = kind(parent)
    result.setContentsMargins(margin, margin, margin, margin)
    result.setSpacing(spacing)
    return result


def image(source):
    if isinstance(source, QPixmap):
        return source
    if isinstance(source, bytes):
        pix = QPixmap()
        if source:
            pix.loadFromData(source)
        return pix
    return QPixmap(str(source)) if source else QPixmap()


def cover(painter, rect, pix, focus=0.25):
    if pix.isNull() or rect.isEmpty():
        return
    ratio = max(rect.width() / pix.width(), rect.height() / pix.height())
    w, h = rect.width() / ratio, rect.height() / ratio
    painter.drawPixmap(rect, pix, QRectF((pix.width() - w) / 2, (pix.height() - h) * focus, w, h))


def search_icon():
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    with QPainter(pix) as p:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(t.MUTED), 2))
        p.drawEllipse(QRectF(5, 5, 15, 15))
        p.drawLine(18, 18, 27, 27)
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def catalog_icon(data):
    pix = image(data)
    if pix.isNull():
        return QIcon()
    thumb = pix.scaled(96, 64, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    return QIcon(thumb.copy((thumb.width() - 96) // 2, round((thumb.height() - 64) * 0.25), 96, 64))


def fade(painter, rect, stops, vertical=False, color="#28282b"):
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft() if vertical else rect.topRight())
    for stop, alpha in stops:
        ink = QColor(color)
        ink.setAlpha(alpha)
        gradient.setColorAt(stop, ink)
    painter.fillRect(rect, gradient)


class ArtPanel(QFrame):
    def __init__(self, deck=False):
        super().__init__()
        self.pix = QPixmap()
        self.deck = deck

    def set_art(self, source):
        self.pix = image(source)
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
            area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            clip = QPainterPath()
            clip.addRoundedRect(area, 6, 6)
            p.setClipPath(clip)
            p.fillRect(area, QColor("#28282b"))
            width = min(550, area.width() * 0.49) if self.deck else area.width() * 0.58
            left = area.width() * 0.52 - width / 2 if self.deck else area.width() - width
            rect = QRectF(left, 0, width, area.height())
            cover(p, rect, self.pix, 0 if self.deck else 0.10)
            stops = (
                ((0, 255), (0.17, 205), (0.38, 49), (0.62, 38), (0.79, 100), (1, 255))
                if self.deck
                else ((0, 255), (0.08, 250), (0.26, 193), (0.43, 76), (0.63, 9), (1, 0))
            )
            fade(p, rect, stops)
            if not self.deck:
                fade(p, rect, ((0, 0), (0.54, 0), (0.72, 65), (0.79, 150), (0.92, 215), (1, 230)), True)
            else:
                p.setPen(QPen(QColor("#414044"), 1))
                p.drawPath(clip)


class MetricIcon(QWidget):
    def __init__(self, index):
        super().__init__()
        self.index = index
        self.setFixedSize(16, 16)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(t.MUTED), 1.3))
            if self.index == 0:
                p.drawRoundedRect(QRectF(2, 3.5, 12, 10), 1.5, 1.5)
                p.drawLine(2, 7, 14, 7)
                p.drawLine(5, 2, 5, 5)
                p.drawLine(11, 2, 11, 5)
            elif self.index == 1:
                p.drawRoundedRect(QRectF(3, 1.5, 10, 13), 1, 1)
                p.drawLine(5, 6, 10, 6)
                p.drawLine(5, 9, 11, 9)
            else:
                p.drawPolygon([QPointF(1, 5), QPointF(8, 1.5), QPointF(15, 5), QPointF(8, 8.5)])
                for y in (8, 11):
                    p.drawPolyline([QPointF(1, y), QPointF(8, y + 3.5), QPointF(15, y)])


class Hero(ArtPanel):
    def __init__(self):
        super().__init__()
        self.setObjectName("formatHero")
        body = layout(self, margin=20, spacing=4)
        top = QHBoxLayout()
        self.title = label("Formats", "heroTitle")
        self.title.setObjectName("selectedFormatTitle")
        self.title.setStyleSheet("font-size:38px;")
        top.addWidget(self.title)
        self.era = label("", "eraTag")
        top.addWidget(self.era, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()
        body.addLayout(top)
        self.description = label("", "body")
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        body.addWidget(self.description, 1)
        meta = QHBoxLayout()
        meta.setSpacing(4)
        self.values = []
        self.fields = []
        for i, name in enumerate(("Snapshot", "Banlist", "Card Pool")):
            field = QWidget()
            field.setFixedWidth(134)
            self.fields.append(field)
            col = layout(field, spacing=4)
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(MetricIcon(i))
            row.addWidget(label(name, "muted"))
            row.addStretch()
            col.addLayout(row)
            value = label("—", "profileValue")
            col.addWidget(value)
            self.values.append(value)
            meta.addWidget(field)
        meta.addStretch()
        body.addLayout(meta)
        body.addSpacing(6)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        body.addLayout(self.actions)
        self.caption = label("", "captionTitle")
        self.caption.setParent(self)
        self.set_name = label("", "captionSet")
        self.set_name.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wide = self.width() >= 1350
        self.title.setStyleSheet("font-size:46px;" if wide else "font-size:38px;")
        self.layout().setContentsMargins(28 if wide else 22, 12 if wide else 10, 20, 16)
        self.layout().setSpacing(4)
        self.description.setMaximumWidth(min(640, round(self.width() * 0.49)))
        for field in self.fields:
            field.setFixedWidth(151 if wide else 134)
        self._caption_geometry()

    def _caption_geometry(self):
        for item, y, h in ((self.caption, self.height() - 56, 26), (self.set_name, self.height() - 30, 20)):
            item.setGeometry(self.width() // 2, y, self.width() // 2 - 20, h)
            item.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_profile(self, row):
        self.title.setText(row["name"])
        self.era.setText(f"{row['region']} / {row['era']}")
        desc = row.get("description") or row.get("event") or row.get("reason") or ""
        sentences = desc.split(". ")
        excerpt = (
            ". ".join([sentences[1], sentences[-1]])
            if row["id"] == "hat" and len(sentences) > 2
            else ". ".join(sentences[:1])
        )
        self.description.setText(excerpt.rstrip(".") + "." if excerpt else "")
        self.description.setToolTip(desc)
        self.values[0].setText(pretty_date(row.get("snapshot_date") or row.get("date")) if row.get("date") else "Rolling")
        self.values[1].setText(row.get("banlist") or "—")
        pool = row.get("pool")
        self.values[2].setText(f"{pool:,} Cards" if isinstance(pool, int) else "—")

    def set_illustration(self, source, caption):
        self.set_art(source)
        self.caption.setText(caption[0])
        self.caption.setToolTip(caption[0])
        self.set_name.setToolTip(caption[1])
        self.set_name.setText(caption[1])
        self._caption_geometry()


class FormatTile(QPushButton):
    def __init__(self, row):
        super().__init__()
        self.row = row
        self.pix = QPixmap()
        self.setText(row["name"])
        self.setAccessibleName(f"{row['name']}, {pretty_date(row['date'])}")
        self.setObjectName("tile-" + row["id"])
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(82)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            clip = QPainterPath()
            clip.addRoundedRect(rect, 4, 4)
            p.setClipPath(clip)
            p.fillPath(clip, QColor(t.PANEL))
            cover(p, rect, self.pix, 0.21)
            shade = "#302d28" if self.isChecked() else "#28282b"
            fade(p, rect, ((0, 2), (0.26, 5), (0.45, 65), (0.64, 205), (0.86, 248), (1, 255)), True, shade)
            edge = t.GOLD if self.isChecked() or self.hasFocus() else t.MUTED if self.underMouse() else t.EDGE
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(edge), 2 if self.isChecked() else 1))
            p.drawPath(clip)
            p.setPen(QColor(t.GOLD if self.isChecked() else t.INK))
            font = t.font(15, bold=True)
            font.setFamilies(["Outfit", t.UI])
            p.setFont(font)
            p.drawText(9, 62, p.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width() - 16))
            p.setFont(t.font(10))
            p.setPen(QColor(t.TEXT))
            p.drawText(9, 76, pretty_date(self.row["date"], "%b %Y"))


STYLE = f"""
QLabel[role="eraTag"] {{color:{t.GOLD};background:#38342d;border:1px solid #4f4737;border-radius:4px;padding:3px 9px;
    font-size:12px;}}
QLabel[role="captionTitle"] {{font-family:Outfit,Inter;font-weight:600;font-size:19px;color:#fff;}}
QLabel[role="captionSet"] {{font-size:12px;color:#e1ded9;}}
QLabel[role="profileValue"] {{font-size:14px;font-weight:600;color:{t.INK};}}
QLabel[role="warning"] {{color:{t.RED};}}
QLabel[role="ok"] {{color:{t.GREEN};}}
QPushButton[role="source"], QPushButton[role="back"] {{background:transparent;border:0;color:{t.GOLD};padding:3px;
    font-size:12px;}}
QPushButton[role="source"]:hover,QPushButton[role="back"]:hover {{color:{t.PALE};}}
QPushButton[role="segment"] {{border-radius:3px;padding:5px 12px;font-size:13px;background:{t.CANVAS};}}
QPushButton[role="segment"]:checked {{background:{t.ACTIVE};color:{t.GOLD};border-color:#706046;}}
QTableWidget {{background:{t.CANVAS};border:0;color:{t.TEXT};
    selection-color:{t.GOLD};selection-background-color:#363127;outline:none;}}
QTableWidget::item {{padding:0px 7px;border-bottom:1px solid #333335;}}
QTableWidget::item:hover {{background:#302e2b;}}
QHeaderView::section {{background:{t.CANVAS};color:{t.MUTED};border:0;border-bottom:1px solid {t.EDGE};padding:4px 7px;
    font-size:12px;}}
QFrame#deckSummary {{ background:#252527; border:1px solid #414044;border-radius:6px; }}
QPushButton#saveAs::menu-indicator {{ image:none;width:0; }}
QComboBox {{padding:4px 8px;background:{t.PANEL};border:1px solid {t.EDGE};border-radius:4px;color:{t.TEXT};}}
QComboBox::drop-down {{border:none;width:20px;}}
QCheckBox {{font-size:12px;spacing:5px;}}
QCheckBox::indicator {{width:12px;height:12px;border:1px solid #77777b;border-radius:2px;background:{t.CANVAS};}}
QCheckBox::indicator:checked {{background:{t.GOLD};border-color:{t.GOLD};
    image:url("{(t.ASSETS / "icons/check.svg").as_posix()}");}}
"""

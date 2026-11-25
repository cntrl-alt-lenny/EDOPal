"""The duel HUD. The top-level surface is independent of the EDOPal shell."""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QScrollArea, QToolTip, QWidget

from edopal.ui import theme as t

from .windows import gamescope_session, mark_external

ZONES = ("Hand", "Field", "GY", "Banished")
ROW = 26
SPENT_ROW = 34
HEADER = 92
WIDTH = 292
ALIGN = Qt.AlignmentFlag


def percent(value):
    return "—" if value is None else f"{100 * value:.1f}".removesuffix(".0") + "%"


def sorted_cards(state, names):
    def key(code):
        chance = state.probability(code)
        return (-(chance if chance is not None else -1), -state.remaining[code], names.get(code, str(code)).casefold())

    live = sorted(state.remaining, key=key)
    spent = sorted(state.starting.keys() - state.remaining.keys(), key=lambda c: names.get(c, str(c)).casefold())
    return live, spent


def dock_rect(game, side, collapsed, height):
    gap = min(12, max(0, (min(game.width(), game.height()) - 40) // 2))
    w = min(170 if collapsed else WIDTH, game.width() - 2 * gap)
    top = min(max(0, game.height() - 40), max(gap, min(60, game.height() // 14)))
    h = min(36 if collapsed else height, game.height() - top - gap)
    x = game.left() + gap if side == "left" else game.right() - w + 1 - gap
    return QRect(x, game.top() + top, w, h)


def logical_rect(bounds, physical):
    rect = QRect(bounds.x, bounds.y, bounds.width, bounds.height)
    if physical:
        # Qt leaves screen origins in native coordinates on mixed-DPI desktops.
        screens = QGuiApplication.screens()
        for screen in screens:
            geo, dpr = screen.geometry(), screen.devicePixelRatio()
            native = QRect(geo.topLeft(), geo.size() * dpr)
            if native.contains(rect.center()):
                return QRect(geo.topLeft() + (rect.topLeft() - geo.topLeft()) / dpr, rect.size() / dpr)
    return rect


class CardRows(QWidget):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.setMouseTracking(True)
        self.setAccessibleName("Remaining cards, copies left and next-draw chance")

    def set_cards(self, state, names, art):
        self.rows.clear()
        live, spent = sorted_cards(state, names)
        self.art = art
        y = 0
        for code in live:
            self.rows.append(
                (
                    y,
                    ROW,
                    code,
                    names.get(code, f"Card {code}"),
                    str(state.remaining[code]),
                    percent(state.probability(code)),
                    "",
                )
            )
            y += ROW
        if spent:
            self.rows.append((y, ROW, 0, f"Out of Deck  ·  {len(spent)}", "", "", ""))
            y += ROW
        for code in spent:
            locations = []
            known = 0
            for zone in (*ZONES, "Material", "Extra"):
                count = state.zones.get(zone, {}).get(code, 0)
                if count:
                    locations.append(f"{zone} ×{count}" if count > 1 else zone)
                    known += count
            if known < state.starting[code]:
                locations.append("Location unknown")
            self.rows.append(
                (
                    y,
                    SPENT_ROW,
                    code,
                    names.get(code, f"Card {code}"),
                    "0",
                    "0%" if state.total else "—",
                    " · ".join(locations),
                )
            )
            y += SPENT_ROW
        self.setFixedHeight(y)
        self.update()

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            for y, height, code, name, _, _, zone in self.rows:
                if code and y <= event.pos().y() < y + height:
                    QToolTip.showText(event.globalPos(), name + (f" · {zone}" if zone else ""), self)
                    return True
        return super().event(event)

    def paintEvent(self, event):
        w = self.width()
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            for y, h, code, name, copies, chance, zone in self.rows:
                if y + h < event.rect().top() or y > event.rect().bottom():
                    continue
                if not code:
                    p.fillRect(0, y, w, h, QColor(88, 84, 78, 80))
                    p.setPen(QColor(t.TEXT))
                    p.setFont(t.font(11, bold=True))
                    p.drawText(QRect(10, y, w - 20, h), ALIGN.AlignVCenter, name)
                    continue
                p.fillRect(0, y, w, 1, QColor(255, 255, 255, 22))
                if (image := self.art.get(code)) is not None:
                    p.setOpacity(0.46 if zone else 1)
                    src_h = min(image.height(), image.width() * (h - 1) / 53)
                    src = QRectF(0, (image.height() - src_h) / 2, image.width(), src_h)
                    p.drawPixmap(QRectF(0, y + 1, 53, h - 1), image, src)
                    p.setOpacity(1)
                    grad = QLinearGradient(34, 0, 54, 0)
                    grad.setColorAt(0, QColor(27, 27, 30, 0))
                    grad.setColorAt(1, QColor(27, 27, 30, 245))
                    p.fillRect(34, y + 1, 20, h - 1, grad)
                left = 55
                p.setPen(QColor("#b0aeac" if zone else t.INK))
                p.setFont(t.font(12))
                space = w - left - 85
                title = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, space)
                p.drawText(QRect(left, y + (2 if zone else 0), space, 16 if zone else h), ALIGN.AlignVCenter, title)
                if zone:
                    p.setPen(QColor(t.MUTED))
                    p.setFont(t.font(10))
                    p.drawText(QRect(left, y + 16, w - left - 12, 17), ALIGN.AlignVCenter, zone)
                p.setFont(t.font(12))
                p.setPen(QColor(t.MUTED if zone else t.TEXT))
                p.drawText(QRect(w - 85, y, 25, h), ALIGN.AlignCenter, copies)
                p.setPen(QColor(t.MUTED if zone else t.GOLD))
                p.drawText(QRect(w - 50, y, 43, h), ALIGN.AlignRight | ALIGN.AlignVCenter, chance)


class DeckPanel(QFrame):
    toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("duelPanel")
        self.setStyleSheet("""
            QFrame#duelPanel { background:rgba(25,25,28,234); border:1px solid #81725a; border-radius:6px; }
            QPushButton#duelTitle { background:transparent; padding:0; border:none; text-align:left; }
            QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; border:none; }
            QScrollBar:vertical { background:#232325; width:4px; margin:0; }
            QScrollBar::handle:vertical { background:#716854; min-height:22px; border-radius:2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        """)
        self.title = QPushButton(self)
        self.title.setObjectName("duelTitle")
        self.title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.title.clicked.connect(self.toggled)
        self.title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.viewport().setAutoFillBackground(False)
        self.rows = CardRows()
        self.scroll.setWidget(self.rows)
        self.rows.setAutoFillBackground(False)
        self.state = None
        self.collapsed = False

    def set_cards(self, state, names, art):
        self.state = state
        self.rows.set_cards(state, names, art)
        self.update()

    def collapse(self, value):
        self.collapsed = value
        self.title.setAccessibleName("Expand deck tracker" if value else "Collapse deck tracker")
        self.scroll.setVisible(not value)
        self.update()

    def resizeEvent(self, event):
        self.title.setGeometry(0, 0, self.width(), 36)
        self.scroll.setGeometry(2, HEADER, self.width() - 4, max(0, self.height() - HEADER - 3))
        super().resizeEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.state is None:
            return
        w = self.width()
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            t.icon("tracker", t.GOLD).paint(p, QRect(10, 9, 19, 19))
            p.setFont(t.font(18, serif=True))
            p.setPen(QColor(t.INK))
            p.drawText(QRect(36, 0, w - 58, 36), ALIGN.AlignVCenter, f"Deck · {self.state.total} left")
            p.setPen(QPen(QColor(t.GOLD), 1.6))
            y = 19 if self.collapsed else 17
            dy = 3 if self.collapsed else -3
            p.drawLine(w - 22, y, w - 18, y + dy)
            p.drawLine(w - 18, y + dy, w - 14, y)
            if self.collapsed:
                return
            cell = (w - 12) // 4
            for i, zone in enumerate(ZONES):
                box = QRect(6 + i * cell, 36, cell, 17)
                p.setFont(t.font(15, bold=True))
                p.setPen(QColor(t.INK))
                p.drawText(box, ALIGN.AlignCenter, str(sum(self.state.zones.get(zone, {}).values())))
                p.setFont(t.font(10))
                p.setPen(QColor(t.TEXT))
                p.drawText(box.translated(0, 15), ALIGN.AlignCenter, zone)
            p.fillRect(1, 72, w - 2, 20, QColor(96, 85, 67, 95))
            p.setPen(QColor(t.GOLD))
            p.setFont(t.font(10, bold=True))
            order = " · Private Order" if not self.state.random_order else ""
            p.drawText(QRect(9, 72, w - 98, 20), ALIGN.AlignVCenter, "Remaining Cards" + order)
            # Match the numeric columns in the scroll viewport.
            offset = self.scroll.verticalScrollBar().width() if self.scroll.verticalScrollBar().isVisible() else 0
            p.drawText(QRect(w - 88 - offset, 72, 27, 20), ALIGN.AlignCenter, "Left")
            p.drawText(QRect(w - 62 - offset, 72, 53, 20), ALIGN.AlignRight | ALIGN.AlignVCenter, "Next Draw")


class DuelOverlay(QWidget):
    collapse_changed = Signal(bool)

    def __init__(self, *, gamescope=None):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setWindowTitle("EDOPal · Deck Tracker")
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.gamescope = gamescope_session() if gamescope is None else gamescope
        self.panel = DeckPanel(self)
        self.panel.toggled.connect(lambda: self.collapse_changed.emit(not self.panel.collapsed))
        self.side = "right"
        self.marked = None

    def configure(self, side, passthrough, collapsed):
        self.side = side
        self.panel.collapse(collapsed)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, passthrough or self.gamescope)

    def follow(self, game):
        if game is None or not self.panel.state:
            self.hide()
            return
        height = HEADER + self.panel.rows.height() + 3
        box = dock_rect(game, self.side, self.panel.collapsed, height)
        if self.gamescope:
            screen = QApplication.screenAt(game.center()) or QApplication.primaryScreen()
            canvas = screen.geometry()
            self.setGeometry(canvas)
            self.panel.setGeometry(box.translated(-canvas.topLeft()))
            if QApplication.platformName() == "xcb" and self.marked != int(self.winId()):
                try:
                    mark_external(int(self.winId()))
                except (OSError, subprocess.SubprocessError) as exc:
                    self.hide()
                    raise RuntimeError("Gamescope could not attach the deck tracker.") from exc
                self.marked = int(self.winId())
        else:
            self.setGeometry(box)
            self.panel.setGeometry(self.rect())
        if not self.isVisible():
            self.show()

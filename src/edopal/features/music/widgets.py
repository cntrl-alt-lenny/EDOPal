"""Music gallery controls painted in logical pixels using the shared design tokens."""

from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QApplication, QFrame, QPushButton, QScrollArea, QSizePolicy, QToolTip, QWidget

from edopal.ui import theme as t
from edopal.ui.widgets.art import draw_cover

_ICONS = {
    "left": '<path d="m14 6-6 6 6 6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "right": '<path d="m10 6 6 6-6 6"/>',
    "play": '<path d="m8 4 12 8-12 8z" fill="currentColor" stroke="none"/>',
    "pause": '<path d="M7 5h3v14H7zm7 0h3v14h-3z" fill="currentColor" stroke="none"/>',
    "volume": '<path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    "mute": '<path d="M3 9h4l5-4v14l-5-4H3zM16 9l5 6m0-6-5 6"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
}


def icon(name: str, color: str = t.TEXT) -> QIcon:
    if name == "restore":
        return t.icon("undo", color)
    body = _ICONS[name].replace("currentColor", color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    pix = QPixmap(48, 48)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pix)


def icon_button(name: str, description: str, *, color=t.TEXT, parent=None) -> QPushButton:
    button = QPushButton(parent)
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(t.CONTROL_H, t.CONTROL_H)
    button.setStyleSheet("padding: 0px;")
    button.setAccessibleName(description)
    button.setToolTip(description)
    return button


def _text(painter, rect, text, size, color=t.INK, *, bold=False, align=Qt.AlignmentFlag.AlignLeft):
    painter.setFont(t.font(size, bold=bold))
    painter.setPen(QColor(color))
    label = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(rect.width())))
    painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | align, label)


def metadata(album):
    return "  ·  ".join(str(value) for value in (album.platform, album.year) if value)


def track_label(title):
    """Make lowercase filename slugs readable without altering the on-disk track."""
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", title):
        return title.replace("-", " ").title()
    return title


def mmss(seconds):
    minutes, seconds = divmod(int(max(0, seconds)), 60)
    return f"{minutes}:{seconds:02d}"


class Carousel(QScrollArea):
    """Horizontal scrolling also works over the covers, without activating a dragged cover."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setStyleSheet("""
            QScrollArea, QScrollArea > QWidget > QWidget {background:transparent;border:0}
            QScrollBar:horizontal {background:#333336;height:5px;margin:0}
            QScrollBar::handle:horizontal {background:#918366;min-width:24px;border-radius:2px}
            QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0}
            QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {background:transparent}
        """)
        self.origin = None
        self.dragging = False
        self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.Wheel:
            delta = event.pixelDelta()
            if delta.isNull():
                delta = event.angleDelta()
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - (delta.x() or delta.y()))
            event.accept()
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.globalPosition().x()
            self.start_value = self.horizontalScrollBar().value()
            self.dragging = False
        elif kind == QEvent.Type.MouseMove and self.origin is not None:
            dx = event.globalPosition().x() - self.origin
            if abs(dx) >= QApplication.startDragDistance():
                self.dragging = True
            if self.dragging:
                self.horizontalScrollBar().setValue(self.start_value - round(dx))
                return True
        elif kind == QEvent.Type.MouseButtonRelease:
            self.origin = None
            if self.dragging:
                self.dragging = False
                if isinstance(obj, QAbstractButton):
                    obj.setDown(False)
                return True
        return super().eventFilter(obj, event)


class Banner(QWidget):
    def __init__(self, *, favorite=False):
        super().__init__()
        self.favorite = favorite
        self.pix = QPixmap()

    def set_image(self, pix):
        self.pix = pix
        self.update()

    def paintEvent(self, event):
        box = QRectF(self.rect())
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            path = QPainterPath()
            path.addRoundedRect(box, 4, 4)
            p.setClipPath(path)
            p.fillRect(box, QColor("#303033" if self.favorite else "#28272a"))
            if self.pix.isNull():
                return
            if self.favorite:
                p.setOpacity(0.085)
                draw_cover(p, self.pix, box)
                p.setOpacity(1)
                lane = max(1, round(box.width() * 0.377))
                overlay = QPixmap(lane, self.height())
                overlay.fill(Qt.GlobalColor.transparent)
                with QPainter(overlay) as layer:
                    area = QRectF(overlay.rect())
                    layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    self._crop(layer, area, 0.70, 0.44)
                    layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    mask = QLinearGradient(area.topLeft(), area.topRight())
                    for position, alpha in ((0, 12), (0.26, 19), (0.53, 55), (0.67, 105), (0.84, 118), (1, 0)):
                        mask.setColorAt(position, QColor(0, 0, 0, alpha))
                    layer.fillRect(area, mask)
                p.drawPixmap(0, 0, overlay)
                if self.property("selected"):
                    p.fillRect(0, 8, 2, self.height() - 16, QColor(t.GOLD))
                return
            self._crop(p, box, 0.66, 0.45)
            horizontal = QLinearGradient(box.topLeft(), box.topRight())
            for offset, alpha in ((0, 235), (0.16, 202), (0.4, 122), (0.66, 35), (1, 8)):
                horizontal.setColorAt(offset, QColor(38, 36, 38, alpha))
            p.fillRect(box, horizontal)
            shade = QLinearGradient(box.topLeft(), box.bottomLeft())
            for position, alpha in ((0, 0), (0.44, 0), (0.58, 100), (0.72, 218), (1, 241)):
                shade.setColorAt(position, QColor(38, 36, 38, alpha))
            p.fillRect(box, shade)

    def _crop(self, painter, area, x, y):
        scale = max(area.width() / self.pix.width(), area.height() / self.pix.height())
        w, h = area.width() / scale, area.height() / scale
        source = QRectF((self.pix.width() - w) * x, (self.pix.height() - h) * y, w, h)
        painter.drawPixmap(area, self.pix, source)


_JACKET_TITLES = {
    "world-championship-2004": "WC Tournament 2004",
    "world-championship-2007": "WC 2007",
    "world-championship-2008": "WC 2008",
    "gx-beginning-of-destiny": "Beginning of Destiny",
    "online-duel-evolution": "Duel Evolution",
    "online-duel-accelerator": "Duel Accelerator",
}


class CoverTile(QAbstractButton):
    moved = Signal(int)

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.album = None
        self.pix = QPixmap()
        self.selected = self.wide = False

    def set_album(self, album, pix):
        self.album, self.pix = album, pix
        self.setAccessibleName(album.game)
        self.setToolTip(f"{album.game}\n{metadata(album)}")
        self.update()

    def keyPressEvent(self, event):
        moves = {Qt.Key.Key_Left: -2, Qt.Key.Key_Right: 2, Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}
        if event.key() in moves:
            self.moved.emit(moves[event.key()])
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        if not self.album:
            return
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            w = self.width()
            h = self.height() - (57 if self.wide else 45)
            rect = QRectF(1, 1, w - 2, h - 2)
            focus = self.property("keyboardFocus")
            p.setPen(QPen(QColor(t.GOLD if self.selected or focus else "#555456"), 2 if self.selected or focus else 1))
            p.setBrush(QColor("#262629"))
            p.drawRoundedRect(rect, 3, 3)
            if not self.pix.isNull():
                area = rect.adjusted(1, 1, -1, -1)
                size = self.pix.deviceIndependentSize().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF(area.topLeft(), size)
                target.moveCenter(area.center())
                p.drawPixmap(target, self.pix, QRectF(self.pix.rect()))
            name = self.album.name.removeprefix("GX ").removeprefix("5D's ").removeprefix("ZEXAL ")
            name = _JACKET_TITLES.get(self.album.id.removeprefix("music-"), name)
            p.setFont(t.font(13 if self.wide else 12))
            p.setPen(QColor(t.GOLD if self.selected else t.INK))
            metrics = p.fontMetrics()
            lines = name.split()
            first = ""
            while lines and (not first or metrics.horizontalAdvance(first + " " + lines[0]) <= w):
                first = (first + " " + lines.pop(0)).strip()
            for i, line in enumerate((first, " ".join(lines))):
                text = metrics.elidedText(line, Qt.TextElideMode.ElideRight, w)
                p.drawText(
                    QRectF(0, h + 3 + i * (15 if self.wide else 12), w, metrics.height()), Qt.AlignmentFlag.AlignCenter, text
                )
            series = self.album.series.replace("SEVENS / Rush", "Rush Duel")
            detail = " · ".join(str(s) for s in (series, self.album.year) if s)
            _text(
                p,
                QRectF(0, self.height() - 16, w, 15),
                detail,
                11 if self.wide else 10,
                t.MUTED,
                align=Qt.AlignmentFlag.AlignCenter,
            )


class TrackRow(QPushButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QPushButton {background:transparent;border:0;border-top:1px solid #514d50;border-radius:0}")
        self.title = self.scene = ""

    def set_track(self, track, selected, scene):
        self.title = track_label(track.title) if track.role else track.title
        self.scene = scene
        self.setToolTip(self.title)
        self.setAccessibleName(f"Preview {self.title}")
        self.setChecked(selected)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        with QPainter(self) as p:
            w, h = self.width(), self.height()
            ink = t.GOLD if self.isChecked() else t.TEXT
            _text(p, QRectF(1, 0, 18, h), "▶", 11, ink)
            _text(p, QRectF(23, 0, w - 124, h), self.title, 13, ink)
            _text(p, QRectF(w - 101, 0, 100, h), self.scene, 11, t.MUTED, align=Qt.AlignmentFlag.AlignRight)


SCENE_TIPS = {
    "menu": "Plays on the main menu.",
    "deck": "Plays while building or editing a deck.",
    "duel": "Plays during a duel.",
    "win": "Plays after winning a duel.",
    "lose": "Plays after losing a duel.",
}


class SceneRow(QAbstractButton):
    """Shared column positions keep the heading, tooltips and all five actions aligned."""

    def __init__(self, role="", title="Scene"):
        super().__init__()
        self.role, self.title = role, title
        self.assignment = "Assigned Track"
        self.game, self.year = "Game", "Year"
        self.active = False
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if role else Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor if role else Qt.CursorShape.ArrowCursor)
        if not role:
            self.setToolTip("Hover over a scene’s information icon to see where its music plays.")

    def set_assignment(self, name, pool, selected, active, game, year):
        self.assignment = name if active else track_label(name)
        if len(pool) > 1:
            self.assignment = f"{self.assignment} +{len(pool) - 1}" if active else f"{len(pool)} local tracks"
        self.game, self.year, self.active = game, str(year or "—"), active
        tracks = "\n".join(track_label(track) for track in pool)
        tip = f"{SCENE_TIPS[self.role]}\n{tracks or 'No tracks in this folder.'}"
        if len(pool) > 1:
            tip += "\nEDOPro picks randomly from these tracks."
        if selected:
            tip += f"\nAssign {selected} to {self.title}."
        self.setToolTip(tip)
        self.setAccessibleName(f"{self.title}, {self.assignment}")
        self.setAccessibleDescription(tip)
        self.update()

    def event(self, event):
        if self.role and event.type() == QEvent.Type.ToolTip and abs(event.pos().x() - self.width() * 0.224) < 16:
            QToolTip.showText(event.globalPos(), SCENE_TIPS[self.role], self)
            return True
        return super().event(event)

    def paintEvent(self, event):
        with QPainter(self) as p:
            w, h = self.width(), self.height()
            wide = w > 710
            ink = t.MUTED if not self.role else t.INK
            if self.role:
                if self.active or self.property("keyboardFocus") or (self.underMouse() and self.isEnabled()):
                    p.fillRect(self.rect(), QColor(t.ACTIVE if self.active else t.HOVER))
                p.fillRect(0, 0, w, 1, QColor("#424144"))
                if self.active:
                    p.fillRect(0, 6, 2, h - 12, QColor(t.GOLD))
            starts = [5, 0.215 * w, 0.276 * w, 0.575 * w, 0.836 * w, 0.955 * w]
            labels = [self.title, "ⓘ", self.assignment, self.game, self.year]
            for i, text in enumerate(labels):
                if i == 1:
                    cx, cy = round(starts[i] + 6), h // 2
                    p.setRenderHint(QPainter.RenderHint.Antialiasing)
                    p.setPen(QPen(QColor(t.GOLD if self.active else t.MUTED), 1))
                    p.drawEllipse(QRectF(cx - 5.5, cy - 5.5, 11, 11))
                    p.drawLine(cx, cy, cx, cy + 3)
                    p.drawPoint(cx, cy - 3)
                    continue
                color = t.GOLD if self.active and i == 2 else ink if i < 3 else t.MUTED
                _text(
                    p,
                    QRectF(starts[i], 0, starts[i + 1] - starts[i] - 3, h),
                    text,
                    (14 if wide else 12) if self.role else (12 if wide else 11),
                    color,
                )
            if self.role and self.isEnabled():
                _text(
                    p,
                    QRectF(starts[-1], 0, w - starts[-1], h),
                    "✓" if self.active else "⊞",
                    15,
                    t.GOLD if self.active else t.MUTED,
                )


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.values = ()
        self.fraction = 0
        self.setMinimumHeight(19)
        self.setAccessibleName("Preview progress")

    def paintEvent(self, event):
        with QPainter(self) as p:
            w, h = self.width(), self.height()
            count = max(1, w // 4)
            for i in range(count):
                amp = self.values[i * len(self.values) // count] if self.values else 0
                height = max(2, round((h - 2) * amp))
                p.fillRect(
                    i * w // count, (h - height) // 2, 2, height, QColor(t.GOLD if i / count < self.fraction else "#847d79")
                )

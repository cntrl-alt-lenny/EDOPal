"""Distinct, looping rows of original and HD artwork comparisons."""

import time
from math import ceil

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .showcase_art import CARDS, atlas_bytes

GOLD = "#ebc987"
FG = "#f4f3f0"
PANEL = "#29292c"
BG = "#202022"
SUB = "#c4c3c3"
DRIFT = 14  # Pixels per second.


def font(size, heading=False):
    f = QFont("Outfit" if heading else "Inter")
    f.setPixelSize(size)
    f.setWeight(QFont.Weight.DemiBold if heading else QFont.Weight.Medium)
    if heading:
        f.setVariableAxis(QFont.Tag.fromString("wght"), 600)
    return f


def pix(data):
    p = QPixmap()
    p.loadFromData(data)
    return p


def cover(painter, image, box):
    scale = max(box.width() / image.width(), box.height() / image.height())
    w, h = box.width() / scale, box.height() / scale
    x, y = (image.width() - w) / 2, (image.height() - h) / 2
    painter.drawPixmap(box, image, QRectF(x, y, w, h))


def badge(p, x, y, text, color):
    p.setFont(font(11))
    box = QRectF(x, y, p.fontMetrics().horizontalAdvance(text) + 14, 18)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#222225"))
    p.drawRoundedRect(box, 3, 3)
    p.setPen(QColor(color))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def curated():
    atlas = pix(atlas_bytes())
    return [(name, atlas.copy(0, i * 222, 288, 222), atlas.copy(288, i * 222, 288, 222)) for i, name in enumerate(CARDS)]


def showcase_rows(pairs, examples):
    artwork = {}
    for name, original, hd in pairs:
        before, after = pix(original), pix(hd)
        if not before.isNull() and not after.isNull():
            artwork.setdefault(name.strip().casefold(), (name, before, after))
    for item in examples:
        artwork.setdefault(item[0].casefold(), item)
    items = list(artwork.values())
    return [items[i::3] for i in range(3)]


class Filmstrip(QWidget):
    def __init__(self, number, direction):
        super().__init__()
        self.direction = direction
        self.offset = (0.0, 0.4, 0.7)[number - 1]
        self.items = []
        self.drag = None
        self.anchor = None
        self.speed = 0.0
        self.wide = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip("Hover to pause. Shift + wheel or drag to browse. Middle-click for manual auto-scroll.")
        self.setAccessibleName(f"HD Showcase · Row {number}")
        self.timer = QTimer(self)
        self.timer.setInterval(32)
        self.timer.timeout.connect(self.advance)

    @property
    def gap(self):
        return 12 if self.wide else 9

    @property
    def stride(self):
        normal = 366 if self.wide else 273
        # Keep the trailing copy of a tile beyond the viewport until its leading copy leaves.
        minimum = ceil((self.width() - self.gap) / (len(self.items) - 1)) if len(self.items) > 1 else 0
        return max(normal, minimum)

    def showEvent(self, event):
        self.tick = time.monotonic()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def advance(self):
        now = time.monotonic()
        dt, self.tick = now - self.tick, now
        if self.drag is None and (self.anchor is not None or not self.underMouse()):
            speed = self.speed if self.anchor is not None else self.direction * DRIFT
            self.offset += dt * speed / self.stride
            self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            px, angle = e.pixelDelta(), e.angleDelta()
            delta = px.x() or px.y() or (angle.x() or angle.y()) / 3
            self.offset -= delta / self.stride
            self.anchor = None
            self.update()
            e.accept()
        else:
            e.ignore()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self.anchor = e.position().x() if self.anchor is None else None
            self.speed = 0.0
        elif e.button() == Qt.MouseButton.LeftButton:
            self.anchor = None
            self.drag = e.position().x()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.drag is not None:
            self.offset += (self.drag - x) / self.stride
            self.drag = x
            self.update()
        elif self.anchor is not None:
            self.speed = (x - self.anchor) * 2

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag = None
            self.unsetCursor()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Escape):
            self.anchor = None
            self.offset += {Qt.Key.Key_Left: -0.25, Qt.Key.Key_Right: 0.25}.get(e.key(), 0)
            self.update()
        else:
            super().keyPressEvent(e)

    def visible_tiles(self):
        if not self.items:
            return
        stride = self.stride
        tile_width = stride - self.gap
        at = self.offset % len(self.items)
        index = int(at)
        x = -(at - index) * stride
        while x < self.width():
            if x + tile_width > 0:
                yield x, self.items[index % len(self.items)]
            x += stride
            index += 1

    def paintEvent(self, event):
        if not self.items:
            return
        width = self.stride - self.gap
        footer = 28 if self.wide else 22
        h = self.height() - footer
        half = (width - 2) / 2
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
            for x, (name, original, hd) in self.visible_tiles():
                for xx, img, tag in ((x, original, "Original"), (x + half + 2, hd, "HD")):
                    cover(p, img, QRectF(xx, 0, half, h))
                    badge(p, xx + 7, h - 24, tag, GOLD if tag == "HD" else FG)
                p.fillRect(QRectF(x, h, width, footer), QColor(PANEL))
                p.setPen(QColor(FG))
                p.setFont(font(15 if self.wide else 12))
                p.drawText(
                    QRectF(x + 11, h, width - 22, footer),
                    Qt.AlignmentFlag.AlignVCenter,
                    p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, width - 22),
                )
            for left, right, rev in ((0, 11, True), (self.width() - 11, self.width(), False)):
                fade = QLinearGradient(left, 0, right, 0)
                fade.setColorAt(0 if rev else 1, QColor(BG))
                fade.setColorAt(1 if rev else 0, QColor(32, 32, 34, 0))
                p.fillRect(QRectF(left, 0, 11, self.height()), fade)

"""Random card banners and the two independently scrollable comparison strips."""

import json
import random
import time
from threading import Lock
from weakref import WeakKeyDictionary

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from .showcase_art import atlas_bytes

GOLD = "#ebc987"
FG = "#f4f3f0"
PANEL = "#29292c"
BG = "#202022"
SUB = "#c4c3c3"
CURATED = ("Pot of Greed", "Dark Magician", "Winged Kuriboh", "Jinzo", "Elemental HERO Neos", "Dark Magician Girl")
PACK = "art-card-of-the-day"
_launch = WeakKeyDictionary()
_lock = Lock()


def load_banner(core, ctx, preview=False, *, previous=None):
    folder = core.packs.cached(PACK)
    if folder is None:
        pack = next((p for p in core.packs.catalog("art") if p.id == PACK), None)
        if pack is None:
            return None
        folder = core.packs.ensure(pack, cancel=lambda: ctx.cancelled)
    rows = json.loads((folder / "banners.json").read_text(encoding="utf-8"))
    records = {r["id"]: r for r in rows if type(r["id"]) is int and (folder / "banners" / f"{r['id']}.jpg").is_file()}
    if not records:
        return None
    with _lock:
        cid = 38033121 if preview and 38033121 in records else _launch.get(core.settings)
        if cid not in records or previous is not None:
            rotation = core.settings.get("hdcards", "banner_rotation", {})
            seen = records.keys() & set(rotation.get("seen", ()))
            recent = rotation.get("recent", core.settings.get("hdcards", "recent_banners", []))
            if previous in records:
                seen.add(previous)
                recent = [*recent, previous] if not recent or recent[-1] != previous else recent
            if seen == records.keys():
                seen.clear()
            remaining = records.keys() - seen
            window = min(12, len(records) - 1)
            choices = remaining - set(recent[-window:]) if window else remaining
            choices = choices or remaining
            cid = random.choice(sorted(choices))
            data = (folder / "banners" / f"{cid}.jpg").read_bytes()
            core.settings.set("hdcards", "banner_rotation", {"seen": sorted(seen | {cid}), "recent": [*recent[-11:], cid]})
            _launch[core.settings] = cid
            return records[cid], data
    return records[cid], (folder / "banners" / f"{cid}.jpg").read_bytes()


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


def cover(painter, image, box, focus=(0.5, 0.5)):
    if image.isNull():
        return
    scale = max(box.width() / image.width(), box.height() / image.height())
    w, h = box.width() / scale, box.height() / scale
    x = max(0, min(image.width() - w, image.width() * focus[0] - w / 2))
    y = max(0, min(image.height() - h, image.height() * focus[1] - h / 2))
    painter.drawPixmap(box, image, QRectF(x, y, w, h))


def badge(p, x, y, text, color=FG, height=18):
    p.setFont(font(11 if height == 18 else 12))
    box = QRectF(x, y, p.fontMetrics().horizontalAdvance(text) + 14, height)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#222225"))
    p.drawRoundedRect(box, 3, 3)
    p.setPen(QColor(color))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class DiceButton(QAbstractButton):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Reroll Random Card")
        self.setToolTip("Reroll Random Card")
        self.setEnabled(False)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#3a3428" if self.isDown() else "#222225"))
            p.drawRoundedRect(QRectF(self.rect()), 5, 5)
            color = QColor(GOLD if self.isEnabled() and (self.underMouse() or self.hasFocus()) else FG)
            if not self.isEnabled():
                color = QColor("#aaa9ac")
            p.setPen(QPen(color, 1.45))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(8, 8, 16, 16), 3, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            for x, y in ((12, 12), (20, 12), (16, 16), (12, 20), (20, 20)):
                p.drawEllipse(QPointF(x, y), 1.15, 1.15)


class Banner(QAbstractButton):
    def __init__(self):
        super().__init__()
        self.record = {}
        self.image = QPixmap()
        self.setAccessibleName("Random Card")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reroll = DiceButton(self)

    def display(self, result):
        if result:
            self.record, data = result
            self.image = pix(data)
            caption = f"{self.record['name']} · {self.record['set_name']} ({self.record['set_code']})"
            self.setToolTip(f"View Card Details · {caption}")
            self.setAccessibleDescription(caption)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        else:
            self.setToolTip("Random card artwork is unavailable.")
        self.reroll.setEnabled(bool(self.record))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reroll.move(self.width() - self.reroll.width() - 10, 10)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            box = QRectF(self.rect())
            path = QPainterPath()
            path.addRoundedRect(box, 6, 6)
            p.setClipPath(path)
            p.fillPath(path, QColor(PANEL))
            if self.record:
                cover(p, self.image, box, self.record.get("focus", (0.5, 0.5)))
                wide = self.height() > 145
                plate = 57 if wide else 47
                y = self.height() - plate
                grad = QLinearGradient(0, y - 9, 0, self.height())
                for where, alpha in ((0, 0), (9 / (plate + 9), 230), (1, 245)):
                    grad.setColorAt(where, QColor(19, 19, 23, alpha))
                p.fillRect(QRectF(0, y - 9, self.width(), plate + 9), grad)
                for title, top, size, heading in (
                    (self.record["name"], y + 2, 21 if wide else 17, True),
                    (f"{self.record['set_name']} ({self.record['set_code']})", y + plate / 2 + 1, 13 if wide else 11, False),
                ):
                    p.setFont(font(size, heading))
                    p.setPen(QColor(FG if heading else SUB))
                    width = self.width() - 24
                    p.drawText(
                        QRectF(12, top, width, plate / 2),
                        Qt.AlignmentFlag.AlignVCenter,
                        p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, width),
                    )
            badge(p, 10, 15, "Random Card", height=22)
            if self.hasFocus():
                p.setPen(QPen(QColor(GOLD), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(box.adjusted(1, 1, -1, -1), 5, 5)


def curated():
    atlas = pix(atlas_bytes())
    return [(name, atlas.copy(0, i * 222, 288, 222), atlas.copy(288, i * 222, 288, 222)) for i, name in enumerate(CURATED)]


class Filmstrip(QWidget):
    def __init__(self, direction):
        super().__init__()
        self.direction = direction
        self.offset = 0.0 if direction == 1 else 2.75
        self.items = []
        self.drag = None
        self.anchor = None
        self.speed = 0.0
        self.wide = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip("Shift + wheel or drag to scroll. Middle-click for horizontal auto-scroll.")
        self.setAccessibleName("HD Showcase · " + ("upper row" if direction == 1 else "lower row"))
        self.timer = QTimer(self)
        self.timer.setInterval(32)
        self.timer.timeout.connect(self.advance)

    @property
    def stride(self):
        return 366 if self.wide else 273

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
        if self.drag is None:
            speed = self.speed if self.anchor is not None else self.direction * 14
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

    def paintEvent(self, event):
        if not self.items:
            return
        width = 354 if self.wide else 264
        footer = 28 if self.wide else 22
        h = self.height() - footer
        half = (width - 2) / 2
        at = self.offset % len(self.items)
        index, x = int(at), -(at - int(at)) * self.stride
        with QPainter(self) as p:
            p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
            while x < self.width():
                name, original, hd = self.items[index % len(self.items)]
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
                x += self.stride
                index += 1
            for left, right, rev in ((0, 11, True), (self.width() - 11, self.width(), False)):
                fade = QLinearGradient(left, 0, right, 0)
                fade.setColorAt(0 if rev else 1, QColor(BG))
                fade.setColorAt(1 if rev else 0, QColor(32, 32, 34, 0))
                p.fillRect(QRectF(left, 0, 11, self.height()), fade)

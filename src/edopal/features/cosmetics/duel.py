"""Render the texture picker and duel from the same decoded PNG samples."""

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QSizePolicy, QWidget

from edopal.ui import theme as t

# Positions are in the 620 × 430 field crop shipped with the stock pack.
MARKERS = {
    "chaintarget": (405, 130, 33),
    "target": (273, 165, 35),
    "act": (499, 273, 39),
    "attack": (183, 323, 45),
    "negated": (384, 328, 38),
    "chain": (286, 325, 44),
    "number": (286, 325, 22),
    "equip": (166, 404, 31),
    "lim": (211, 300, 20),
}


def sample(key, path):
    pix = QPixmap(str(path))
    if key in ("number", "lim"):
        pix = pix.copy(64, 0, 64, 64)
    # Trim transparent margins for the matrix and field overlays.
    if pix.hasAlphaChannel():
        bounds = QRegion(pix.mask()).boundingRect()
        if not bounds.isEmpty():
            return pix.copy(bounds)
    return pix


def fit(bounds, pix):
    size = pix.deviceIndependentSize().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
    rect = QRectF(bounds.topLeft(), size)
    rect.moveCenter(bounds.center())
    return rect


class DuelPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setAccessibleName("Live Duel Preview")
        self.field = QPixmap()
        self.samples = {}
        self.choices = {}
        self.scene = QPixmap()

    def sizeHint(self):
        return QSize(620, 430)

    def paintEvent(self, event):
        if not self.scene.isNull():
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(fit(QRectF(self.rect()), self.scene), self.scene, QRectF(self.scene.rect()))

    def closeup(self, key, size):
        if self.scene.isNull():
            return QPixmap()
        if key == "lpf":
            region = QRect(8, 15, 260, 32)
        elif key == "settings":
            region = QRect(560, 0, 60, 53)
        else:
            x, y, _ = MARKERS[key]
            region = QRect(x - 32, min(430 - 56, y - 28), 64, 56)
        return self.scene.copy(region).scaled(
            size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )

    def compose(self, choices):
        if self.field.isNull():
            return
        self.choices = choices.copy()
        self.scene = self.field.copy()
        with QPainter(self.scene) as p:
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            for key, (x, y, size) in MARKERS.items():
                self.draw_sample(p, key, QRectF(x - size / 2, y - size / 2, size, size))
            p.fillRect(0, 0, 620, 48, QColor("#202427"))
            for i, (name, lp) in enumerate((("You", 7000), ("Opponent", 4000))):
                x = 12 + i * 282
                p.setFont(t.font(14))
                p.setPen(QColor(t.TEXT))
                p.drawText(QRectF(x, 1, 252, 21), Qt.AlignmentFlag.AlignVCenter, name)
                p.fillRect(x, 25, 252, 16, QColor("#333a3e"))
                p.fillRect(x + 3, 28, round(246 * lp / 8000), 10, QColor("#578678"))
                self.draw_sample(p, "lpf", QRectF(x, 24, 252, 18), fill=True)
                p.setPen(QColor(t.INK))
                p.setFont(t.font(14, bold=True))
                p.drawText(QRectF(x, 22, 252, 21), Qt.AlignmentFlag.AlignCenter, str(lp))
            self.draw_sample(p, "settings", QRectF(578, 12, 24, 24))

        self.update()

    def draw_sample(self, painter, key, rect, *, fill=False):
        pix = self.samples.get((key, self.choices.get(key)))
        if pix and not pix.isNull():
            painter.drawPixmap(rect if fill else fit(rect, pix), pix, QRectF(pix.rect()))

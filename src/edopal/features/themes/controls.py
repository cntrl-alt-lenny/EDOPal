"""Presentation widgets for the live preview and the two card-back slots."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from edopal.ui import theme as t


def label(text: str, role: str) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setProperty("role", role)
    return widget


def icon(shape: str, color: str = t.TEXT) -> QIcon:
    if shape in {"undo", "check", "customize"}:
        return t.icon("settings" if shape == "customize" else shape, color)
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(color), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if shape == "close":
        painter.drawLine(6, 6, 18, 18)
        painter.drawLine(18, 6, 6, 18)
    elif shape == "chevron":
        painter.drawPolyline([QPointF(9, 6), QPointF(15, 12), QPointF(9, 18)])
    painter.end()
    return QIcon(pixmap)


def thumbnail(path: Path | str | None, size: QSize) -> QPixmap:
    if not path:
        return QPixmap()
    reader = QImageReader(str(path))
    natural = reader.size()
    if natural.isValid() and (natural.width() > size.width() or natural.height() > size.height()):
        reader.setScaledSize(natural.scaled(size, Qt.AspectRatioMode.KeepAspectRatio))
    return QPixmap.fromImage(reader.read())


class Portrait(QWidget):
    def __init__(self, width=46, height=66):
        super().__init__()
        self.setFixedSize(width, height)
        self._image = QPixmap()
        self._source = None

    def set_image(self, path: Path | None):
        try:
            stat = path.stat() if path else None
        except OSError:
            stat = None
        source = (path, (stat.st_mtime_ns, stat.st_size) if stat else None)
        if self._source != source:
            self._source = source
            self._image = thumbnail(path, self.size() * 3)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect()).adjusted(0.6, 0.6, -0.6, -0.6)
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 3.5, 3.5)
        painter.setClipPath(clip)
        painter.fillRect(bounds, QColor(t.RAIL))
        if self._image.isNull():
            painter.setPen(QColor(t.EDGE))
            painter.drawRoundedRect(bounds.adjusted(4, 4, -4, -4), 2, 2)
        else:
            painter.drawPixmap(bounds, self._image, QRectF(self._image.rect()))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(t.EDGE))
        painter.drawPath(clip)


class CardSlot(QPushButton):
    def __init__(self, slot: str):
        super().__init__()
        self.slot = slot
        self.setObjectName(f"slot-{slot}")
        self.setProperty("role", "scope")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(15, 5, 16, 5)
        row.setSpacing(15)
        self.portrait = Portrait()
        row.addWidget(self.portrait)
        details = QVBoxLayout()
        details.setSpacing(2)
        details.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.owner = label(slot.upper(), "goldKicker")
        self.title = label("", "subheading")
        self.caption = label("", "muted")
        for item in (self.owner, self.title, self.caption):
            item.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            details.addWidget(item)
        row.addLayout(details, 1)
        arrow = QLabel()
        arrow.setPixmap(icon("chevron", t.MUTED).pixmap(18, 18))
        row.addWidget(arrow)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_back(self, name: str, caption: str, path: Path | None):
        self.title.setText(name)
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        self.portrait.set_image(path)
        self.setAccessibleName(f"Choose card back for {self.slot}. {name}")
        self.setToolTip(f"{name} · {caption}" if caption else name)


class Hero(QFrame):
    """A softly lit surround sampled from the selected pack's tile artwork."""

    def __init__(self):
        super().__init__()
        self._art = QPixmap()
        self._path = None

    def set_art(self, path: Path | str | None):
        if path == self._path:
            return
        self._path = path
        source = thumbnail(path, QSize(160, 100))
        self._art = (
            source.scaled(22, 14, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if not source.isNull()
            else source
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        outline = QPainterPath()
        outline.addRoundedRect(rect, t.R_PANEL, t.R_PANEL)
        painter.setClipPath(outline)
        painter.fillRect(rect, QColor(t.PANEL))
        if not self._art.isNull():
            painter.setOpacity(0.68)
            painter.drawPixmap(rect, self._art, QRectF(self._art.rect()))
            painter.setOpacity(1)
        shade = QLinearGradient(rect.topLeft(), rect.bottomRight())
        for stop, alpha in ((0, 115), (0.58, 135), (1, 35)):
            color = QColor(t.RAIL)
            color.setAlpha(alpha)
            shade.setColorAt(stop, color)
        painter.fillRect(rect, shade)
        painter.setPen(QColor(t.EDGE))
        painter.drawPath(outline)

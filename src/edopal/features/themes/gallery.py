"""Image gallery controls for the Themes page.

Stock previews were captured in EDOPro 41.0.2 with the built-in metallic skin.
The original backgrounds are © 2020 LogicalNonsense (GNU AGPLv3 or later).
Field and gear textures are © 2020 Icematoro (GNU AGPLv3 or later).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from edopal.ui import theme

ImageSource = QPixmap | Path | str | None


def stock_preview(scene: str) -> str:
    """Original EDOPro menu artwork for the tile, or the corresponding game capture."""
    if scene not in {"menu", "deck", "duel", "tile"}:
        raise ValueError(f"Unknown preview scene: {scene}")
    from ._stock_resources import PREFIX

    return f"{PREFIX}/{scene}.webp"


def _pixmap(source: ImageSource) -> QPixmap:
    if isinstance(source, QPixmap):
        return source
    return QPixmap(str(source)) if source else QPixmap()


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _centered(rect: QRectF, aspect: float) -> QRectF:
    width = min(rect.width(), rect.height() * aspect)
    height = width / aspect
    return QRectF(rect.center().x() - width / 2, rect.center().y() - height / 2, width, height)


class PreviewCanvas(QWidget):
    """Fit the complete game view into a 16:10 window and crossfade scene changes."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAccessibleName("Game preview")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image = QPixmap()
        self._previous = QPixmap()
        self._label = "Preview unavailable"
        self._loading = False
        self._opacity = 1.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(theme.MOTION_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade.valueChanged.connect(self._set_opacity)
        self._fade.finished.connect(self._finish_fade)

    def sizeHint(self):
        return QSize(560, 350)

    def set_preview(self, image: ImageSource, *, label: str = "Preview unavailable", animate: bool = True) -> None:
        self.set_loading(False)
        next_image = _pixmap(image)
        self.setAccessibleDescription(label)
        if next_image.cacheKey() == self._image.cacheKey() and label == self._label:
            if not animate:
                self._fade.stop()
                self._finish_fade()
            return
        previous = self._image
        if animate and not self._previous.isNull() and self._opacity < 1:
            frame = self._frame()
            previous = QPixmap(frame.size().toSize())
            previous.fill(QColor(theme.RAIL))
            with QPainter(previous) as painter:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                self._draw_images(painter, QRectF(previous.rect()))
        self._fade.stop()
        self._image, self._label = next_image, label
        if animate and self.isVisible() and not previous.isNull() and not next_image.isNull():
            self._previous, self._opacity = previous, 0.0
            self._fade.start()
        else:
            self._finish_fade()

    def set_loading(self, loading: bool) -> None:
        if self._loading != loading:
            self._loading = loading
            self.update()

    def _frame(self) -> QRectF:
        return _centered(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16 / 10)

    def _set_opacity(self, opacity: float):
        self._opacity = opacity
        self.update()

    def _finish_fade(self):
        self._previous = QPixmap()
        self._set_opacity(1.0)

    def _draw_images(self, painter: QPainter, frame: QRectF):
        for pixmap, opacity in ((self._previous, 1.0), (self._image, self._opacity)):
            if not pixmap.isNull():
                painter.setOpacity(opacity)
                painter.drawPixmap(_centered(frame, pixmap.width() / pixmap.height()), pixmap, QRectF(pixmap.rect()))
        painter.setOpacity(1.0)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            frame = self._frame()
            outline = _rounded(frame, theme.R_SMALL)
            painter.fillPath(outline, QColor(theme.RAIL))
            painter.setClipPath(outline)
            if self._image.isNull():
                painter.setFont(theme.font(theme.SMALL))
                painter.setPen(QColor(theme.MUTED))
                painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, self._label)
            else:
                self._draw_images(painter, frame)
                if self._loading:
                    # Keep the outgoing scene for the transition, with its status visible during a slow fetch.
                    painter.setFont(theme.font(theme.SMALL))
                    text = "Loading preview…"
                    width = QFontMetrics(painter.font()).horizontalAdvance(text) + 20
                    tag = QRectF(frame.left() + 10, frame.bottom() - 34, width, 24)
                    painter.fillPath(_rounded(tag, theme.R_SMALL), QColor(theme.RAIL))
                    painter.setPen(QColor(theme.TEXT))
                    painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(theme.EDGE), 1))
            painter.drawPath(outline)


class ThemeTile(QPushButton):
    """Keyboard-accessible theme tile with an independent preview-on-hover signal."""

    hovered = Signal(str)

    def __init__(self, pack_id: str, name: str, tagline: str, parent: QWidget | None = None):
        super().__init__(name, parent)
        self.pack_id = pack_id
        self._tagline = tagline
        self._thumbnail = QPixmap()
        self._badge = ""
        self._previewed = False
        self.setAccessibleName(name)
        self.setAccessibleDescription(tagline)
        self.setCheckable(True)
        self.setProperty("role", "scope")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self):
        return QSize(208, 146)

    def minimumSizeHint(self):
        return QSize(104, 94)

    def set_thumbnail(self, image: ImageSource):
        self._thumbnail = _pixmap(image)
        self.update()

    def set_previewed(self, previewed: bool):
        if self._previewed != previewed:
            self._previewed = previewed
            self.update()

    def set_badge(self, text: str):
        self._badge = text
        self.setAccessibleDescription(". ".join(filter(None, (self._tagline, text))))
        self.setToolTip(" · ".join(filter(None, (self.text(), self._tagline, text))))
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.hovered.emit(self.pack_id)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hovered.emit("")
        self.update()

    def nextCheckState(self):
        self.setChecked(True)

    def _draw_badge(self, painter: QPainter, image_rect: QRectF):
        if not self._badge:
            return
        active = self._badge.casefold() == "active"
        downloaded = self._badge.casefold() in {"downloaded", "built-in"}
        face = theme.font(11, bold=True)
        painter.setFont(face)
        metrics = QFontMetrics(face)
        label = metrics.elidedText(self._badge, Qt.TextElideMode.ElideRight, max(20, int(image_rect.width()) - 43))
        width = metrics.horizontalAdvance(label) + 29
        rect = QRectF(image_rect.right() - width - 8, image_rect.top() + 7, width, 20)
        fill = QColor(theme.RAIL)
        fill.setAlpha(230)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 10, 10)
        fg = QColor(theme.GREEN if active else theme.INK)
        painter.setPen(QPen(fg, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = rect.x() + 11, rect.center().y()
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fg)
            painter.drawEllipse(QPointF(x, y), 2.7, 2.7)
        elif downloaded:
            painter.drawPolyline([QPointF(x - 3, y), QPointF(x - 1, y + 2), QPointF(x + 3.5, y - 2.5)])
        else:
            painter.drawLine(QPointF(x, y - 3.5), QPointF(x, y + 2.5))
            painter.drawPolyline([QPointF(x - 2.5, y), QPointF(x, y + 2.5), QPointF(x + 2.5, y)])
        painter.setPen(fg)
        painter.drawText(rect.adjusted(19, 0, -5, 0), Qt.AlignmentFlag.AlignVCenter, label)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            outline = _rounded(rect, theme.R_PANEL)
            previewing = self.underMouse() or self._previewed
            painter.fillPath(outline, QColor(theme.HOVER if previewing else theme.PANEL))
            painter.setClipPath(outline)
            compact = self.height() < 110
            caption_height = 30 if compact else 54
            photo = rect.adjusted(0, 0, 0, -caption_height)
            painter.fillRect(photo, QColor(theme.RAIL))
            if not self._thumbnail.isNull():
                source = _centered(QRectF(self._thumbnail.rect()), photo.width() / max(1, photo.height()))
                painter.drawPixmap(photo, self._thumbnail, source)
            self._draw_badge(painter, photo)
            left = rect.left() + 12
            room = int(rect.width() - 24)
            for text, top, size, bold, color in (
                (self.text(), photo.bottom() + 5, 16, True, theme.INK),
                (self._tagline, photo.bottom() + 26, theme.SMALL, False, theme.MUTED),
            ):
                # The preview already shows the tagline. Give short tiles room for their artwork.
                if compact and not bold:
                    continue
                face = theme.font(size, bold=bold)
                painter.setFont(face)
                painter.setPen(QColor(color))
                label = QFontMetrics(face).elidedText(text, Qt.TextElideMode.ElideRight, room)
                painter.drawText(QRectF(left, top, room, 20), Qt.AlignmentFlag.AlignVCenter, label)
            painter.setClipping(False)
            border = theme.TEXT if previewing else theme.EDGE
            border_width = 1.5 if previewing else 1.0
            if self.isChecked():
                border = theme.GOLD
            focused = self.property("keyboardFocus")
            if focused:
                border = theme.PALE
            painter.setPen(QPen(QColor(border), 2 if focused or self.isChecked() else border_width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(outline)

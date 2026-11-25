"""Image gallery controls for the Themes page.

Stock previews were captured in EDOPro 41.0.2 with the built-in metallic skin.
The original backgrounds are © 2020 LogicalNonsense (GNU AGPLv3 or later).
Field and gear textures are © 2020 Icematoro (GNU AGPLv3 or later).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QScrollArea, QSizePolicy, QWidget

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
    """Size the full 16:10 game capture from the available column width."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAccessibleName("Game preview")
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._scene = QPixmap()
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

    def heightForWidth(self, width):
        return round(width * 10 / 16)

    def set_preview(self, image: ImageSource, *, label: str = "Preview unavailable", animate: bool = True) -> None:
        self.set_loading(False)
        next_image = _pixmap(image)
        self.setAccessibleDescription(label)
        if next_image.cacheKey() == self._scene.cacheKey() and label == self._label:
            if not animate:
                self._fade.stop()
                self._finish_fade()
            return
        previous = self._scene
        if animate and not self._previous.isNull() and self._opacity < 1:
            snapshot = QPixmap(self.size())
            snapshot.fill(QColor(theme.RAIL))
            with QPainter(snapshot) as painter:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                self._draw_images(painter, QRectF(snapshot.rect()))
            previous = snapshot
        self._fade.stop()
        self._scene = next_image
        self._label = label
        if animate and self.isVisible() and not previous.isNull() and not self._scene.isNull():
            self._previous, self._opacity = previous, 0.0
            self._fade.start()
        else:
            self._finish_fade()

    def set_loading(self, loading: bool) -> None:
        if self._loading != loading:
            self._loading = loading
            self.update()

    def _set_opacity(self, opacity: float):
        self._opacity = opacity
        self.update()

    def _finish_fade(self):
        self._previous = QPixmap()
        self._set_opacity(1.0)

    def _draw_images(self, painter: QPainter, frame: QRectF):
        for image, opacity in ((self._previous, 1.0), (self._scene, self._opacity)):
            if not image.isNull():
                painter.setOpacity(opacity)
                painter.drawPixmap(frame, image, QRectF(image.rect()))
        painter.setOpacity(1.0)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            frame = QRectF(self.rect())
            if self._scene.isNull():
                painter.fillRect(frame, QColor(theme.RAIL))
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
                    tag = QRectF(frame.left() + 10, frame.top() + 10, width, 24)
                    painter.fillPath(_rounded(tag, theme.R_SMALL), QColor(theme.RAIL))
                    painter.setPen(QColor(theme.TEXT))
                    painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)


class ThemeShelf(QScrollArea):
    def wheelEvent(self, event):
        bar = self.horizontalScrollBar()
        if not bar.maximum():
            event.ignore()
            return
        pixels, steps = event.pixelDelta(), event.angleDelta()
        distance = pixels.x() or pixels.y() or (steps.x() or steps.y()) * bar.singleStep() / 40
        bar.setValue(bar.value() - round(distance))
        event.accept()


class ThemeTile(QPushButton):
    """Keyboard-accessible theme tile with an independent preview-on-hover signal."""

    hovered = Signal(str)
    navigate = Signal(int)

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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(225, 76)

    def minimumSizeHint(self):
        return QSize(205, 54)

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

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.navigate.emit(1 if event.key() == Qt.Key.Key_Right else -1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            outline = _rounded(rect, theme.R_PANEL)
            painter.fillPath(outline, QColor(theme.ACTIVE if self.isChecked() else theme.PANEL))
            painter.setClipPath(outline)
            photo = QRectF(5, 5, min(rect.width() * 0.41, (rect.height() - 8) * 1.9), rect.height() - 8)
            if not self._thumbnail.isNull():
                source = _centered(QRectF(self._thumbnail.rect()), photo.width() / photo.height())
                painter.drawPixmap(photo, self._thumbnail, source)
            left = photo.right() + 12
            width = int(rect.right() - left - 6)
            status = self._badge
            if self.isChecked() and status != "Active":
                status = "Selected · " + status
            for text, top, font, color in (
                (self.text(), rect.center().y() - 22, theme.font(17, serif=True), theme.INK),
                (status, rect.center().y() + 2, theme.font(11), theme.GOLD if self.isChecked() else theme.MUTED),
            ):
                painter.setFont(font)
                painter.setPen(QColor(color))
                painter.drawText(
                    QRectF(left, top, width, 20),
                    Qt.AlignmentFlag.AlignVCenter,
                    QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, width),
                )
            painter.setClipping(False)
            border = theme.GOLD if self.isChecked() else theme.TEXT if self._previewed else theme.EDGE
            painter.setPen(QPen(QColor(theme.PALE if self.hasFocus() else border), 1.5 if self.isChecked() else 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(outline)

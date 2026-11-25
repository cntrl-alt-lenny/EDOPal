"""Feature page headings, surfaces, actions and status feedback."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from edopal.ui import theme as t
from edopal.ui.widgets.art import ImageSource, draw_cover, pixmap


def _label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def page_layout(page: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(page)
    margin = t.CONTENT_MARGIN
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(t.MD)
    return layout


class PageHeader(QWidget):
    """Page identity and contextual controls share a single horizontal band."""

    def __init__(self, kicker: str, title: str):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(t.LG)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        self.kicker = _label(kicker.upper(), "goldKicker")
        identity.addWidget(self.kicker)
        titles = QHBoxLayout()
        titles.setSpacing(t.MD)
        self.title = _label(title, "pageTitle")
        titles.addWidget(self.title)
        self.detail = _label("", "muted")
        titles.addWidget(self.detail, 1, Qt.AlignmentFlag.AlignBaseline)
        titles.setAlignment(self.title, Qt.AlignmentFlag.AlignBaseline)
        identity.addLayout(titles)
        row.addLayout(identity, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(t.SM)
        row.addLayout(self.actions)


class Hero(QFrame):
    def __init__(
        self, kicker: str, title: str, subtitle: str = "", *, art: ImageSource | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(t.XL, t.LG, t.XL, t.LG)
        self.row.setSpacing(0)
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        copy.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.kicker = _label(kicker.upper(), "goldKicker")
        self.title = _label(title, "heroTitle", wrap=True)
        self.subtitle = _label(subtitle, "body", wrap=True)
        for label in (self.kicker, self.title, self.subtitle):
            copy.addWidget(label)
            if not label.text():
                label.hide()
        self.row.addLayout(copy, 3)
        self.row.addStretch()
        self.set_art(art)

    def set_art(self, art: ImageSource | None) -> None:
        self._art = pixmap(art)
        self.row.setStretch(1, 0 if self._art.isNull() else 2)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(box, t.R_PANEL, t.R_PANEL)
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            painter.setClipPath(clip)
            gradient = QLinearGradient(box.topLeft(), box.bottomRight())
            gradient.setColorAt(0, QColor(t.HERO_DARK))
            gradient.setColorAt(1, QColor(t.HERO_LIGHT))
            painter.fillPath(clip, gradient)
            if not self._art.isNull():
                self._paint_art(painter, box, self._art)
            painter.setPen(QPen(QColor(t.EDGE), 1))
            painter.drawPath(clip)

    @staticmethod
    def _paint_art(painter: QPainter, box: QRectF, image: QPixmap) -> None:
        art = QRectF(box.width() * 0.55, box.y(), box.width() * 0.45 + 1, box.height())
        if image.hasAlphaChannel():
            art.adjust(t.MD, t.SM, -t.MD, -t.SM)
            size = image.deviceIndependentSize().scaled(art.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(art.topLeft(), size)
            target.moveCenter(art.center())
            painter.drawPixmap(target, image, QRectF(image.rect()))
        else:
            draw_cover(painter, image, art)
            fade = QLinearGradient(box.topLeft(), box.topRight())
            fade.setColorAt(0, QColor(t.HERO_DARK))
            fade.setColorAt(0.55, QColor(t.HERO_DARK))
            fade.setColorAt(0.82, Qt.GlobalColor.transparent)
            painter.fillRect(box, fade)


class Panel(QFrame):
    def __init__(self, parent: QWidget | None = None, *, padding: int = t.LG):
        super().__init__(parent)
        self.setProperty("role", "panel")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(t.MD)


class _Button(QPushButton):
    role = ""

    def __init__(self, text: str, parent: QWidget | None = None, *, icon: str | None = None):
        super().__init__(text, parent)
        self.setProperty("role", self.role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(t.BUTTON_H)
        if icon:
            self.setIcon(t.icon(icon, t.ON_GOLD if self.role == "primary" else t.TEXT))
            self.setIconSize(QSize(t.ICON, t.ICON))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        ):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class PrimaryButton(_Button):
    role = "primary"


class QuietButton(_Button):
    role = "quiet"


class Notice(QLabel):
    def __init__(self, text: str = "", kind: str = "info", parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("role", "notice")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self._set_content(text, kind)
        if not text:
            self.hide()

    def set_message(self, text: str, kind: str = "info") -> None:
        self._set_content(text, kind)
        self.setVisible(bool(text))

    def _set_content(self, text: str, kind: str) -> None:
        if self.property("kind") != kind:
            self.setProperty("kind", kind)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setText(text)
        self.setAccessibleName(f"{kind.title()}: {text}")


class ProgressBlock(Panel):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout()
        self.label = _label(text, "body", wrap=True)
        self.percent = _label("", "progressPercent")
        row.addWidget(self.label, 1)
        row.addWidget(self.percent)
        self.body.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(t.SM)
        self.body.addWidget(self.bar)
        self.set_progress(0, 0, text)

    def set_progress(self, done: int, total: int, text: str = "") -> None:
        if text:
            self.label.setText(text)
        self.bar.setAccessibleName(self.label.text())
        if total <= 0:
            self.bar.setRange(0, 0)
            self.percent.clear()
        else:
            # Qt's bar accepts 32-bit values. Download byte counts can be larger.
            value = min(1000, max(0, done * 1000 // total))
            self.bar.setRange(0, 1000)
            self.bar.setValue(value)
            self.percent.setText(f"{value // 10}%")

"""Page-local controls for print selection and portrait previews."""

from collections.abc import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSizePolicy, QSlider, QVBoxLayout, QWidget

from studio import theme as t
from studio.icons import icon
from studio.widgets import Button, ElidedLabel, label, margins
from studio.widgets.art import ArchiveArt

from .sleeves_collection import Choice
from .sleeves_work import Slot


class Menu(QComboBox):
    def __init__(self, name: str, names: Iterable[str] = ()) -> None:
        super().__init__()
        self.setAccessibleName(name)
        self.addItems(list(names))
        self.setMinimumHeight(t.BUTTON_H)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"""
            QComboBox {{background:{t.RAISED}; border:{t.HAIR}px solid {t.EDGE};
                border-radius:{t.R_SMALL}px; padding:{t.XS}px {t.SM}px;}}
            QComboBox:focus {{border:{t.FOCUS}px solid {t.PALE};}}
            QComboBox::drop-down {{width:{t.XL}px; border:0;}}
        """)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(t.TEXT), t.FOCUS))
            x, y = self.width() - t.LG, self.height() // 2
            painter.drawLine(x - t.XS, y - 2, x, y + 2)
            painter.drawLine(x, y + 2, x + t.XS, y - 2)


class Slider(QSlider):
    def __init__(self, name: str) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setAccessibleName(name)
        self.setMinimumHeight(t.XL)
        self.setStyleSheet(f"""
            QSlider::groove:horizontal {{height:{t.XS}px; background:{t.EDGE};}}
            QSlider::sub-page:horizontal {{background:{t.BRONZE};}}
            QSlider::handle:horizontal {{background:{t.GOLD}; width:{t.MD}px;
                margin:-{t.XS}px 0; border-radius:{t.R_SMALL}px;}}
            QSlider::handle:horizontal:focus {{background:{t.PALE}; border:{t.FOCUS}px solid {t.INK};}}
        """)


class SleeveSlot(Button):
    def __init__(self, name: str) -> None:
        super().__init__("", "scope")
        self.setAccessibleName(name)
        self.setCheckable(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        row = QHBoxLayout(self)
        margins(row, t.MD)
        self.art = label("")
        self.art.setFixedSize(t.HUGE + t.SM, t.TILE_H)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.art)
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        row.addLayout(copy, 1)
        self.heading = label(name, "subheading")
        self.name = ElidedLabel()
        self.state = label("", "small", True)
        self.previous = ElidedLabel()
        copy.addStretch()
        copy.addWidget(self.heading)
        copy.addWidget(self.name)
        copy.addWidget(self.state)
        copy.addWidget(self.previous)
        copy.addStretch()

    def show_choice(
        self, slot: Slot | None, picked: Choice | None, preview: QImage, has_game: bool
    ) -> None:
        name, state, img = "Game default", "No saved choice", QImage()
        previous = ""
        if slot is None and has_game:
            name, state = "Game sleeve", "Not checked yet"
        if slot:
            img = slot.image
            if slot.choice:
                name = slot.choice.name
            elif slot.status.path:
                name = "Current game sleeve"
            if slot.status.matches is False:
                state = "Changed in game"
            elif slot.status.matches:
                state = "Matches game"
            elif slot.status.path:
                state = "In game"
        if picked:
            if slot:
                previous = (
                    f"Saved · {name} (changed)"
                    if slot.status.matches is False
                    else f"In game · {name}"
                )
            name = picked.full_name
            state, img = "Ready to apply", preview
        elif not has_game:
            name, state = "No game selected", "Choose a folder"
        self.display(name, state, img, previous)

    def display(self, name: str, state: str, image: QImage, previous: str = "") -> None:
        self.name.set_value(name)
        self.state.setText(state)
        self.previous.set_value(previous)
        self.previous.setVisible(bool(previous))
        self.setAccessibleDescription(f"{name}. {state}. {previous}")
        if image.isNull():
            pix = icon("sleeves", t.BRONZE).pixmap(self.art.size())
        else:
            pix = QPixmap.fromImage(image).scaled(
                self.art.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.art.setPixmap(pix)


class SlotBanner(ArchiveArt):
    def __init__(self) -> None:
        super().__init__("sleeves")
        self.setMinimumHeight(0)
        self.setFixedHeight(t.TILE_LARGE)
        row = QHBoxLayout(self)
        row.setContentsMargins(t.XL, t.LG, t.LG, t.LG)
        row.setSpacing(t.MD)
        intro = QVBoxLayout()
        intro.addStretch()
        intro.addWidget(label("APPEARANCE", "goldKicker"))
        self.title = label("Card sleeves", "display")
        intro.addWidget(self.title)
        intro.addWidget(label("Choose a sleeve for yourself or your opponent.", "body", True))
        intro.addStretch()
        row.addLayout(intro, 3)
        self.slots = {}
        for key, name in (("mine", "Your sleeve"), ("opponent", "Opponent’s sleeve")):
            button = SleeveSlot(name)
            self.slots[key] = button
            row.addWidget(button, 4)


class Preview(QWidget):
    moved = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.image = QImage()
        self.caption = "Sleeve preview"
        self.draggable = False
        self.last: QPointF | None = None
        self.setAccessibleName(self.caption)
        self.setMinimumSize(t.TILE_H, t.TILE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, image: QImage) -> None:
        self.image = image
        self.update()

    def set_draggable(self, enabled: bool) -> None:
        self.draggable = enabled
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if enabled else Qt.FocusPolicy.NoFocus)
        self.setAccessibleDescription(
            "Drag or use the arrow keys to move the crop." if enabled else ""
        )

    def card_rect(self) -> QRectF:
        if self.image.isNull():
            return QRectF()
        inset = t.LG + 1
        available = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        size = self.image.size().scaled(
            available.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
        )
        card = QRectF(QPointF(), size)
        card.moveCenter(available.center())
        return card

    def paintEvent(self, event: QPaintEvent) -> None:
        with QPainter(self) as p:
            p.setRenderHints(
                QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
            )
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            p.setBrush(QColor(t.RAIL))
            p.setPen(
                QPen(
                    QColor(t.PALE if self.hasFocus() else t.EDGE),
                    t.FOCUS if self.hasFocus() else t.HAIR,
                )
            )
            p.drawRoundedRect(rect, t.R_PANEL, t.R_PANEL)
            available = rect.adjusted(t.LG, t.LG, -t.LG, -t.LG)
            if self.image.isNull():
                card = QRectF(
                    0,
                    0,
                    min(t.TILE_H, available.width() / 2),
                    min(t.TILE_H * 1.43, available.height() / 2),
                )
                card.moveCenter(available.center() - QPointF(0, t.MD))
                p.setBrush(QColor(t.PANEL))
                p.setPen(QPen(QColor(t.BRONZE), t.HAIR))
                p.drawRoundedRect(card, t.R_SMALL, t.R_SMALL)
                icon("sleeves", t.BRONZE).paint(p, card.adjusted(t.LG, t.LG, -t.LG, -t.LG).toRect())
                p.setPen(QColor(t.MUTED))
                p.setFont(t.font(t.SMALL))
                p.drawText(
                    QRectF(available.left(), card.bottom() + t.SM, available.width(), t.XXL),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    self.caption,
                )
                return
            p.drawImage(self.card_rect(), self.image)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            self.draggable
            and self.card_rect().contains(event.position())
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.last = event.position()
            self.setFocus()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.drag_to(event.position())

    def drag_to(self, position: QPointF) -> None:
        if self.last is not None and self.draggable and position != self.last:
            delta = position - self.last
            self.last = position
            card = self.card_rect()
            self.moved.emit(delta.x() / max(1, card.width()), delta.y() / max(1, card.height()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.drag_to(event.position())
        self.last = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        steps = {
            Qt.Key.Key_Left: (-0.05, 0),
            Qt.Key.Key_Right: (0.05, 0),
            Qt.Key.Key_Up: (0, -0.05),
            Qt.Key.Key_Down: (0, 0.05),
        }
        if self.draggable and event.key() in steps:
            self.moved.emit(*steps[event.key()])
        else:
            super().keyPressEvent(event)

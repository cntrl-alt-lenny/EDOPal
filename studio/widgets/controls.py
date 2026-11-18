from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.icons import icon


class ActivateOnEnter(QObject):
    """Read-only lists use Return to open a row, including on macOS where Qt edits it."""

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        view.installEventFilter(self)

    def eventFilter(self, view: QAbstractItemView, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers()
            in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
            and view.currentIndex().isValid()
        ):
            event.accept()
            view.activated.emit(view.currentIndex())
            return True
        return False


def label(text: str, role: str = "body", wrap: bool = False) -> QLabel:
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setProperty("role", role)
    item.setWordWrap(wrap)
    return item


def margins(layout: QVBoxLayout | QHBoxLayout, amount: int) -> None:
    layout.setContentsMargins(amount, amount, amount, amount)


def glyph(name: str, size: int = t.ICON, colour: str = t.GOLD) -> QLabel:
    view = QLabel()
    view.setFixedSize(size, size)
    view.setPixmap(icon(name, colour).pixmap(QSize(size, size), view.devicePixelRatioF()))
    view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return view


class Button(QPushButton):
    def __init__(self, text: str, role: str = "secondary", symbol: str = "") -> None:
        super().__init__(text)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(t.BUTTON_H)
        if symbol:
            self.setIcon(icon(symbol, t.ON_GOLD if role == "primary" else t.TEXT))
            self.setIconSize(QSize(t.ICON, t.ICON))

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if layout := self.layout():
            return hint.expandedTo(layout.sizeHint())
        return hint

    def minimumSizeHint(self) -> QSize:
        if layout := self.layout():
            return layout.minimumSize()
        return super().minimumSizeHint()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class Panel(QFrame):
    def __init__(self, padding: int = t.XL) -> None:
        super().__init__()
        self.setProperty("role", "panel")
        self.body = QVBoxLayout(self)
        margins(self.body, padding)
        self.body.setSpacing(t.MD)


class SectionHeader(QWidget):
    def __init__(self, title: str, description: str = "") -> None:
        super().__init__()
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.XS)
        body.addWidget(label(title, "heading"))
        if description:
            body.addWidget(label(description, "body", True))


class ActionTile(Button):
    def __init__(self, title: str, hint: str, symbol: str, *, choice: bool = False) -> None:
        super().__init__("", "scope" if choice else "action")
        self.setAccessibleName(title)
        self.setAccessibleDescription(hint)
        self.setMinimumHeight(t.TILE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setCheckable(choice)
        row = QHBoxLayout(self)
        margins(row, t.LG)
        row.setSpacing(t.MD)
        row.addWidget(glyph(symbol, t.ICON_BIG))
        copy = QVBoxLayout()
        copy.setSpacing(t.XS)
        copy.addWidget(label(title, "subheading"))
        self.hint = label(hint, "muted", True)
        copy.addWidget(self.hint)
        row.addLayout(copy, 1)
        if choice:
            self.marker = QLabel()
            self.marker.setFixedSize(t.LG, t.LG)
            row.addWidget(self.marker, 0, Qt.AlignmentFlag.AlignTop)
            self.toggled.connect(self._check)
            self._check(False)
        else:
            row.addWidget(glyph("chevron", t.LG, t.MUTED))
        for item in self.findChildren(QLabel):
            item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def _check(self, checked: bool) -> None:
        edge = t.GOLD if checked else t.MUTED
        fill = t.GOLD if checked else t.CLEAR
        self.marker.setStyleSheet(
            f"border: {t.HAIR}px solid {edge}; background: {fill}; border-radius: {t.SM}px;"
        )
        self.marker.setPixmap(icon("check", t.ON_GOLD).pixmap(t.LG, t.LG) if checked else QPixmap())


class SegmentedControl(QWidget):
    changed = Signal(int)

    def __init__(self, choices: Sequence[tuple[str, str, str]]) -> None:
        super().__init__()
        self.group = QButtonGroup(self)
        row = QHBoxLayout(self)
        margins(row, 0)
        row.setSpacing(t.MD)
        self.buttons = []
        for i, (name, hint, symbol) in enumerate(choices):
            btn = ActionTile(name, hint, symbol, choice=True)
            self.group.addButton(btn, i)
            self.buttons.append(btn)
            row.addWidget(btn, 1)
        self.select(0)
        self.group.idToggled.connect(self._toggled)

    def select(self, index: int) -> None:
        self.buttons[index].setChecked(True)

    def _toggled(self, index: int, checked: bool) -> None:
        if checked:
            self.changed.emit(index)


class ToggleSwitch(Button):
    def __init__(self, title: str, hint: str, checked: bool) -> None:
        super().__init__("", "quiet")
        self.setAccessibleName(title)
        self.setAccessibleDescription(hint)
        self.setCheckable(True)
        self.setChecked(checked)
        body = QVBoxLayout(self)
        body.setContentsMargins(t.MD, t.SM, t.HUGE + t.XL, t.SM)
        body.setSpacing(t.XS)
        for text, role in ((title, "body"), (hint, "muted")):
            line = label(text, role, True)
            line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            body.addWidget(line)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h, w = t.ICON, t.BRAND_ICON
        x, y = self.width() - w - t.MD, (self.height() - h) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.GOLD if self.isChecked() else t.EDGE))
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
        d = h - t.XS * 2
        pos = x + (w - d - t.XS if self.isChecked() else t.XS)
        p.setBrush(QColor(t.ON_GOLD if self.isChecked() else t.TEXT))
        p.drawEllipse(QRectF(pos, y + t.XS, d, d))


class SearchField(QLineEdit):
    def __init__(self, placeholder: str = "Search") -> None:
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setAccessibleName(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icon("search", t.MUTED), QLineEdit.ActionPosition.LeadingPosition)


class SwatchButton(Button):
    colour_changed = Signal(QColor)

    def __init__(self, name: str, colour: QColor) -> None:
        super().__init__(name)
        self.colour = QColor(colour)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self) -> None:
        from PySide6.QtGui import QIcon

        pix = QPixmap(t.ICON, t.ICON)
        pix.fill(self.colour)
        self.setIcon(QIcon(pix))
        self.setAccessibleDescription(self.colour.name())

    def _pick(self) -> None:
        selected = QColorDialog.getColor(self.colour, self, self.text())
        if selected.isValid():
            self.colour = selected
            self._refresh()
            self.colour_changed.emit(selected)


class ElidedLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.value = ""
        self.setProperty("role", "muted")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_value(self, value: str, display: str | None = None) -> None:
        self.value = display if display is not None else value
        self.setAccessibleDescription(value)
        self.setToolTip(value)
        self._fit()

    def _fit(self) -> None:
        self.setText(
            self.fontMetrics().elidedText(self.value, Qt.TextElideMode.ElideMiddle, self.width())
        )

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self._fit()

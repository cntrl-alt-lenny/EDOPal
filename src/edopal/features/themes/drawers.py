"""Theme customization drawer."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from edopal.ui import theme as t

from .controls import icon, label


class Drawer(QWidget):
    dismissed = Signal()

    def __init__(self, parent, title, anchor):
        super().__init__(parent)
        self.setGeometry(parent.rect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.anchor = anchor
        self.panel = QFrame(self)
        self.panel.setObjectName("themesDrawer")
        self.panel.setStyleSheet(
            f"QFrame#themesDrawer {{ background: {t.PANEL}; border: 1px solid {t.EDGE}; border-radius: {t.R_PANEL}px; }}"
        )
        self.layout_box = QVBoxLayout(self.panel)
        self.layout_box.setContentsMargins(18, 16, 18, 18)
        self.layout_box.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(label(title, "subheading"), 1)
        self.close_button = QPushButton()
        self.close_button.setProperty("role", "quiet")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setStyleSheet("padding: 0")
        self.close_button.setIcon(icon("close"))
        self.close_button.setAccessibleName("Close")
        self.close_button.clicked.connect(self.dismissed)
        top.addWidget(self.close_button)
        self.layout_box.addLayout(top)
        self.escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape.activated.connect(self.dismissed)

    def place(self):
        self.setGeometry(self.parentWidget().rect())
        margin = 12
        pos = self.anchor.mapTo(self.parentWidget(), QPoint(0, 0))
        width = min(self.width() - margin * 2, self.preferred_width)
        self.panel.setFixedWidth(width)
        y = pos.y() + self.anchor.height() + 8
        height = min(self.height() - y - margin, self.panel.sizeHint().height())
        left = pos.x() + self.anchor.width() - width
        x = max(margin, min(left, self.width() - width - margin))
        self.panel.setGeometry(x, y, width, height)
        self.raise_()
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        shade = QColor(t.RAIL)
        shade.setAlpha(96)
        painter.fillRect(self.rect(), shade)

    def mousePressEvent(self, event):
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.dismissed.emit()
        event.accept()

    def focusNextPrevChild(self, forward):
        focus = self.focusWidget()
        if focus:
            target = focus.nextInFocusChain() if forward else focus.previousInFocusChain()
            while target != focus:
                if self.panel.isAncestorOf(target) and target.isVisible() and target.isEnabled():
                    if target.focusPolicy() & Qt.FocusPolicy.TabFocus:
                        target.setFocus(Qt.FocusReason.TabFocusReason)
                        return True
                target = target.nextInFocusChain() if forward else target.previousInFocusChain()
        return super().focusNextPrevChild(forward)


class CustomizeDrawer(Drawer):
    saved = Signal(str, bool)

    def __init__(self, parent, anchor, state, name, accent):
        super().__init__(parent, f"Customize {name}", anchor)
        self.preferred_width = 328
        self.setObjectName("customize-drawer")
        self.layout_box.addWidget(label("Accent Colour", "small"))
        colors = [
            (accent or state.accent or t.GOLD, "Theme accent"),
            (t.GOLD, "Gold"),
            (t.GREEN, "Sage"),
            ("#9bbdd7", "Glacier"),
            ("#b3a0d9", "Lilac"),
        ]
        self.color = (state.accent or colors[0][0]).lower()
        if self.color not in {c.lower() for c, _ in colors}:
            colors.append((self.color, "Current accent"))
        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        self.group = QButtonGroup(self)
        added = set()
        for color, name in colors:
            color = color.lower()
            if color in added or not QColor(color).isValid():
                continue
            added.add(color)
            button = QPushButton()
            button.setObjectName(f"swatch-{color[1:]}")
            button.setCheckable(True)
            button.setFixedSize(30, 30)
            button.setIconSize(QSize(16, 16))
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setStyleSheet(
                f"QPushButton {{background: {color}; padding: 0; border: 2px solid {t.PANEL}; border-radius: 15px;}}"
                f"QPushButton:checked, QPushButton[keyboardFocus=true]:focus {{border: 2px solid {t.INK};}}"
            )
            self.group.addButton(button)
            button.toggled.connect(partial(self._color_changed, button, color))
            button.setChecked(color == self.color)
            swatches.addWidget(button)
        swatches.addStretch()
        self.layout_box.addLayout(swatches)
        self.font = QCheckBox("Use Theme Font")
        self.font.setChecked(state.theme_font and state.has_font)
        self.font.setEnabled(state.has_font)
        if not state.has_font:
            self.font.setToolTip("This theme uses the game font.")
        self.layout_box.addWidget(self.font, alignment=Qt.AlignmentFlag.AlignLeft)
        save = QPushButton("Save Changes")
        save.setProperty("role", "primary")
        save.clicked.connect(lambda: self.saved.emit(self.color, self.font.isChecked()))
        self.layout_box.addWidget(save)

    def _color_changed(self, button, color, checked):
        button.setIcon(icon("check", t.ON_GOLD) if checked else QIcon())
        if checked:
            self.color = color

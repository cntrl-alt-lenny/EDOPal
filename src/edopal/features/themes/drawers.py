"""Inline card-back picker and customization controls."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from edopal.ui import theme as t

from .controls import Portrait, icon, label
from .media import card_label

GROUPS = {
    "tcg-ocg": "TCG / OCG",
    "anime": "Anime",
    "manga": "Manga",
    "video-games": "Video games",
    "other-tcgs": "Other card games",
}


class Drawer(QWidget):
    dismissed = Signal()

    def __init__(self, parent, title, anchor, *, above=False):
        super().__init__(parent)
        self.setGeometry(parent.rect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.anchor = anchor
        self.above = above
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
        if self.above:
            low_edge = max(180, pos.y() - 8)
            height = min(low_edge - margin, self.preferred_height)
            y = max(margin, low_edge - height)
        else:
            y = pos.y() + self.anchor.height() + 8
            height = min(self.height() - y - margin, self.panel.sizeHint().height())
        left = pos.x() if self.above and pos.x() < self.width() // 2 else pos.x() + self.anchor.width() - width
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


class CardPicker(Drawer):
    picked = Signal(str)

    def __init__(self, parent, anchor, slot, packs, current, thumbnails, can_apply):
        super().__init__(parent, "Your card back" if slot == "you" else "Opponent’s card back", anchor, above=True)
        self.images = {}
        self.setObjectName("cardback-picker")
        body = QWidget()
        body.setObjectName("backOptions")
        body.setStyleSheet(f"QWidget#backOptions {{background: {t.PANEL};}}")
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 3, 0)
        content.setSpacing(8)
        groups = {}
        for pack in packs:
            groups.setdefault(pack.meta.get("group") or "other", []).append(pack)
        columns = min(3, max((len(items) for items in groups.values()), default=1))
        self.preferred_width = max(350, 32 + 174 * columns)
        self.preferred_height = 77
        for group in sorted(groups, key=lambda g: list(GROUPS).index(g) if g in GROUPS else len(GROUPS)):
            content.addWidget(label(GROUPS.get(group, group.replace("-", " ")).upper(), "kicker"))
            grid = QGridLayout()
            grid.setSpacing(6)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            for index, pack in enumerate(groups[group]):
                button = QPushButton()
                button.setObjectName(f"pick-{pack.id}")
                button.setProperty("role", "scope")
                button.setCheckable(True)
                button.setChecked(pack.id == current)
                button.setEnabled(can_apply)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                name = card_label(pack)
                button.setAccessibleName(f"{pack.name}. {name}")
                button.setToolTip(name)
                layout = QVBoxLayout(button)
                layout.setContentsMargins(6, 8, 6, 7)
                layout.setSpacing(4)
                image = Portrait(60, 86)
                image.set_image(thumbnails.get(pack.id))
                self.images[pack.id] = image
                layout.addWidget(image, alignment=Qt.AlignmentFlag.AlignHCenter)
                title = label(pack.name, "small")
                title.setStyleSheet(f"font-weight: 650; color: {t.INK}")
                detail = label(name, "muted")
                for field in (title, detail):
                    field.setWordWrap(True)
                    field.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    layout.addWidget(field)
                layout.addStretch()
                button.setMinimumHeight(150)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
                for child in button.findChildren(QWidget):
                    child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                button.clicked.connect(partial(self.picked.emit, pack.id))
                grid.addWidget(button, index // columns, index % columns)
            content.addLayout(grid)
            self.preferred_height += 29 + 154 * ((len(groups[group]) + columns - 1) // columns)
        if not groups:
            content.addWidget(label("No card backs available.", "body"))
            self.preferred_height += 50
        self.preferred_height = min(572, self.preferred_height)
        content.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("pickerScroll")
        scroll.setStyleSheet(f"QScrollArea#pickerScroll {{background: {t.PANEL}; border: 0;}}")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.layout_box.addWidget(scroll)


class CustomizeDrawer(Drawer):
    saved = Signal(str, bool)

    def __init__(self, parent, anchor, state, name, accent):
        super().__init__(parent, f"Customize {name}", anchor)
        self.preferred_width = 328
        self.setObjectName("customize-drawer")
        self.layout_box.addWidget(label("Accent colour", "small"))
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
        self.font = QCheckBox("Use theme font")
        self.font.setChecked(state.theme_font and state.has_font)
        self.font.setEnabled(state.has_font)
        if not state.has_font:
            self.font.setToolTip("This theme uses the game font.")
        self.layout_box.addWidget(self.font, alignment=Qt.AlignmentFlag.AlignLeft)
        save = QPushButton("Save changes")
        save.setProperty("role", "primary")
        save.clicked.connect(lambda: self.saved.emit(self.color, self.font.isChecked()))
        self.layout_box.addWidget(save)

    def _color_changed(self, button, color, checked):
        button.setIcon(icon("check", t.ON_GOLD) if checked else QIcon())
        if checked:
            self.color = color

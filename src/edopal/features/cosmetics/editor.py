"""Preset selection, per-slot texture choices, and the composited duel preview."""

from functools import partial

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from edopal.features.themes.controls import label
from edopal.ui import theme as t

from .duel import DuelPreview, sample
from .icons import KEPT, PRESETS, SLOTS, choice_name


class IconEditor(QWidget):
    apply_requested = Signal()

    def __init__(self):
        super().__init__()
        self.library = None
        self.choices = dict.fromkeys(SLOTS, "stock")
        self.original = self.choices.copy()
        self.preset = "stock"
        self.focused = "attack"
        self.buttons = {}
        self.presets = {}
        self.current = {}
        self.rows = {}
        self.can_apply = False
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        presets = QHBoxLayout()
        for slug, name in PRESETS.items():
            button = QPushButton(name)
            button.setCheckable(True)
            button.setProperty("role", "scope")
            button.setMinimumHeight(40)
            button.setIconSize(QSize(23, 23))
            button.setStyleSheet("padding: 0 12px;")
            button.clicked.connect(partial(self.select_preset, slug))
            presets.addWidget(button, 1)
            self.presets[slug] = button
        body.addLayout(presets)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        body.addLayout(columns, 1)

        left = QVBoxLayout()
        left.addWidget(label("Icon Editor", "heading"))
        headers = QGridLayout()
        headers.setContentsMargins(8, 0, 8, 0)
        self._columns(headers)
        for col, title in enumerate(("Icon Slot", "Current", "Stock", *"ABCDE")):
            text = label(title, "small")
            if col:
                text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            headers.addWidget(text, 0, col)
        left.addLayout(headers)
        rows = QVBoxLayout()
        rows.setSpacing(2)
        left.addLayout(rows, 1)
        columns.addLayout(left, 11)
        for key, (title, _) in SLOTS.items():
            frame = QFrame()
            frame.setObjectName("iconRow")
            self.rows[key] = frame
            rows.addWidget(frame, 1)
            grid = QGridLayout(frame)
            grid.setContentsMargins(8, 2, 8, 2)
            self._columns(grid)
            title_label = label(title, "body")
            title_label.setStyleSheet("font-size: 12px;")
            grid.addWidget(title_label, 0, 0)
            current = label("", "small")
            current.setAlignment(Qt.AlignmentFlag.AlignCenter)
            current.setStyleSheet(f"color: {t.GOLD}; font-size: 11px;")
            self.current[key] = current
            grid.addWidget(current, 0, 1)
            choices = tuple(PRESETS) if key in KEPT else ("stock", *"ABCDE")
            kept = None
            if key in KEPT:
                kept = QHBoxLayout()
                kept.setSpacing(2)
                grid.addLayout(kept, 0, 3, 1, 5)
            for i, choice in enumerate(choices):
                button = QPushButton()
                button.setObjectName(f"icon-{key}-{choice}")
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
                button.setMinimumSize(0, 19)
                button.setIconSize(QSize(28, 21))
                button.setCheckable(True)
                button.setAccessibleName(f"{title} · {choice_name(choice)}")
                button.setToolTip(button.accessibleName())
                button.setStyleSheet(
                    f"QPushButton {{padding: 0; border: 1px solid transparent; background: transparent;}}"
                    f"QPushButton:hover {{background: {t.HOVER};}}"
                    f"QPushButton:checked {{background: {t.ACTIVE}; border: 1px solid #9b8054;}}"
                    f"QPushButton:focus {{border-color: {t.PALE};}}"
                )
                if key in KEPT and i:
                    button.setText({"minimal": "Minimal", "minimal-gold": "Gold", "minimal-glass": "Glass"}[choice])
                button.clicked.connect(partial(self.pick, key, choice))
                self.buttons[key, choice] = button
                if kept is not None and i:
                    kept.addWidget(button, 1)
                else:
                    grid.addWidget(button, 0, i + 2)

        right = QVBoxLayout()
        right.addWidget(label("Live Duel Preview", "heading"))
        self.preview = DuelPreview()
        right.addWidget(self.preview, 1)
        focus = QHBoxLayout()
        self.focus_title = label("", "heading")
        self.focus_choice = label("", "small")
        focus.addWidget(self.focus_title, 1)
        focus.addWidget(self.focus_choice)
        right.addLayout(focus)
        info = QHBoxLayout()
        self.focus_image = label("", "")
        self.focus_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.focus_image.setFixedSize(62, 54)
        self.focus_image.setStyleSheet(f"background: {t.PANEL}; border-radius: 4px;")
        info.addWidget(self.focus_image)
        self.description = label("", "muted")
        self.description.setWordWrap(True)
        info.addWidget(self.description, 1)
        right.addLayout(info)
        actions = QHBoxLayout()
        self.reset = QPushButton("Reset to Preset")
        self.reset.clicked.connect(lambda: self.select_preset(self.preset))
        actions.addWidget(self.reset)
        actions.addStretch()
        self.apply = QPushButton("Apply Icons")
        self.apply.setProperty("role", "primary")
        self.apply.clicked.connect(self.apply_requested)
        actions.addWidget(self.apply)
        right.addLayout(actions)
        columns.addLayout(right, 10)
        self.render()

    @staticmethod
    def _columns(grid):
        grid.setHorizontalSpacing(2)
        for col, stretch in enumerate((23, 15, 10, 10, 10, 10, 10, 10)):
            grid.setColumnStretch(col, stretch)

    def set_library(self, library):
        self.library = library
        self.preview.field = QPixmap(str(library.field))
        self.preview.samples = {pair: sample(pair[0], path) for pair, path in library.textures.items()}
        for pair, button in self.buttons.items():
            button.setIcon(QIcon(self.preview.samples[pair]))
        for slug, button in self.presets.items():
            button.setIcon(QIcon(self.preview.samples["chain", slug]))
        self.render()

    def set_installed(self, choices, can_apply, custom, *, reset=False):
        for slot, path in custom.items():
            self.preview.samples[slot, "custom"] = sample(slot, path)
        if reset or self.original != choices:
            self.choices = choices.copy()
            self.preset = max(PRESETS, key=lambda p: sum(c == p for c in choices.values()))
        self.original = choices.copy()
        self.can_apply = can_apply
        self.render()

    def select_preset(self, preset):
        self.preset = preset
        self.choices = dict.fromkeys(SLOTS, preset)
        self.render()

    def pick(self, key, choice):
        self.focused = key
        self.choices[key] = choice
        self.render()

    def render(self):
        for slug, button in self.presets.items():
            button.setChecked(slug == self.preset)
        for (key, choice), button in self.buttons.items():
            button.setChecked(self.choices[key] == choice)
        for key, choice in self.choices.items():
            self.current[key].setText(
                {"stock": "Stock", "custom": "Custom", "minimal-gold": "Gold", "minimal-glass": "Glass"}.get(
                    choice, choice.title()
                )
            )
        for index, (slot, frame) in enumerate(self.rows.items()):
            fill = t.ACTIVE if slot == self.focused else t.PANEL if index % 2 == 0 else "#262628"
            line = t.GOLD if slot == self.focused else fill
            frame.setStyleSheet(f"QFrame#iconRow {{background: {fill}; border-left: 2px solid {line};}}")
        key = self.focused
        choice = self.choices[key]
        self.focus_title.setText(SLOTS[key][0])
        self.focus_choice.setText("Game Texture" if choice == "custom" else choice_name(choice))
        self.description.setText(SLOTS[key][1])
        self.preview.compose(self.choices)
        self.focus_image.setPixmap(self.preview.closeup(key, self.focus_image.size()))
        self.reset.setEnabled(any(v != self.preset for v in self.choices.values()))
        self.apply.setEnabled(self.can_apply and self.choices != self.original)

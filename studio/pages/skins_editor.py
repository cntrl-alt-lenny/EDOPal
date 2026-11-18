"""Inline controls for editing a theme without obscuring its live preview."""

from dataclasses import replace
from functools import partial

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QSlider, QVBoxLayout, QWidget

import themes
from content.media import Media
from studio import theme as t
from studio.widgets import Button, Panel, ToggleSwitch, label, margins

from .skins_model import BASE, EXTRA, surprise
from .sleeves_widgets import Menu


def colour_slider(name: str) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setAccessibleName(name)
    slider.setMinimumHeight(t.XL)
    slider.setStyleSheet(f"""
        QSlider {{ background:transparent; border:{t.HAIR}px solid transparent; }}
        QSlider:focus {{ border-color:{t.BRONZE}; border-radius:{t.R_SMALL}px; }}
        QSlider::groove:horizontal {{ height:{t.XS}px; background:{t.EDGE}; }}
        QSlider::sub-page:horizontal {{ background:{t.BRONZE}; }}
        QSlider::handle:horizontal {{ width:{t.LG}px; background:{t.GOLD};
            margin:-{t.SM}px 0; border-radius:{t.SM}px; }}
    """)
    return slider


class ColourMixer(Panel):
    edited = Signal(str)

    def __init__(self) -> None:
        super().__init__(t.MD)
        top = QHBoxLayout()
        self.title = label("", "subheading")
        self.done = Button("Done", "quiet", "check")
        top.addWidget(self.title, 1)
        top.addWidget(self.done)
        self.body.addLayout(top)
        self.channels: list[QSlider] = []
        for name in ("Red", "Green", "Blue"):
            line = QHBoxLayout()
            caption = label(name, "small")
            caption.setMinimumWidth(t.HUGE)
            slider = colour_slider(name)
            slider.setRange(0, 255)
            slider.valueChanged.connect(self._slide)
            line.addWidget(caption)
            line.addWidget(slider)
            self.body.addLayout(line)
            self.channels.append(slider)
        self.hex = QLineEdit()
        self.hex.setAccessibleName("Hex color")
        self.hex.setMaxLength(7)
        self.hex.editingFinished.connect(self._hex)
        self.body.addWidget(self.hex)
        self.done.clicked.connect(self.hide)

    def select(self, name: str, value: str) -> None:
        self.title.setText(name)
        self._set(value)
        self.show()
        self.channels[0].setFocus()

    def _set(self, value: str) -> None:
        c = QColor(value)
        self.hex.setText(c.name().upper())
        for slider, channel in zip(self.channels, (c.red(), c.green(), c.blue()), strict=True):
            with QSignalBlocker(slider):
                slider.setValue(channel)

    def _slide(self) -> None:
        value = QColor(*(s.value() for s in self.channels)).name()
        self.hex.setText(value.upper())
        self.edited.emit(value)

    def _hex(self) -> None:
        text = self.hex.text().strip()
        colour = QColor(text)
        if len(text) == 7 and text.startswith("#") and colour.isValid():
            self._set(colour.name())
            self.edited.emit(colour.name())
        else:
            self._slide()


class Maker(QWidget):
    changed = Signal()
    choose_image = Signal()
    save = Signal()
    save_as = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.theme = themes.PRESETS[0]
        self.field = "accent"
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.SM)
        body.addWidget(label("Theme name", "small"))
        self.name = QLineEdit()
        self.name.setAccessibleName("Theme name")
        self.name.setMaxLength(64)
        self.name.textChanged.connect(self.changed)
        body.addWidget(self.name)
        body.addWidget(label("Base colors", "subheading"))
        self.swatches: dict[str, Button] = {}
        self.resets: dict[str, Button] = {}
        for key, name in BASE.items():
            body.addWidget(self._swatch(key, name))
        self.mixer = ColourMixer()
        self.mixer.edited.connect(lambda colour: self.update_theme(**{self.field: colour}))
        body.addWidget(self.mixer)
        random = Button("Surprise me", "quiet", "spark")
        self.surprise = random
        random.clicked.connect(lambda: self.set_theme(surprise(self.theme)))
        body.addWidget(random)
        self.fine = Button("Fine tune", "secondary", "settings")
        self.fine.setCheckable(True)
        body.addWidget(self.fine)
        self.advanced = QWidget()
        adv = QVBoxLayout(self.advanced)
        margins(adv, 0)
        adv.setSpacing(t.SM)
        adv.addWidget(label("Color overrides", "subheading"))
        for key, name in EXTRA.items():
            row = QHBoxLayout()
            row.addWidget(self._swatch(key, name), 1)
            reset = Button("Auto", "quiet")
            reset.setAccessibleName(f"Reset {name.lower()} to automatic")
            reset.clicked.connect(partial(self._reset, key))
            row.addWidget(reset)
            self.resets[key] = reset
            adv.addLayout(row)
        self.advanced_mixer = ColourMixer()
        self.advanced_mixer.edited.connect(lambda colour: self.update_theme(**{self.field: colour}))
        adv.addWidget(self.advanced_mixer)
        self.ranges: dict[str, QSlider] = {}
        self.range_labels = {}
        for key, name, maximum in (("roundness", "Corner roundness", 8), ("depth", "Depth", 100)):
            self.range_labels[key] = label(name, "small")
            adv.addWidget(self.range_labels[key])
            slider = colour_slider(name)
            slider.setRange(0, maximum)
            slider.valueChanged.connect(partial(self._number, key))
            self.ranges[key] = slider
            adv.addWidget(slider)
        self.highlight = ToggleSwitch(
            "Highlight the turn player", "Light up the active life bar.", True
        )
        self.highlight.toggled.connect(lambda on: self.update_theme(highlight_turn=on))
        adv.addWidget(self.highlight)
        adv.addWidget(label("Wallpaper", "subheading"))
        styles = QGridLayout()
        self.styles: dict[str, Button] = {}
        for i, style in enumerate(themes.STYLES):
            button = Button(style.title())
            button.setIconSize(QSize(t.HUGE, t.XXL))
            button.clicked.connect(partial(self._wall, style))
            styles.addWidget(button, i // 2, i % 2)
            self.styles[style] = button
        adv.addLayout(styles)
        self.inherit = Button("Use game backgrounds")
        self.inherit.clicked.connect(partial(self._wall, "inherit"))
        adv.addWidget(self.inherit)
        self.photo = Button("Use my own image…", "secondary", "folder")
        self.photo.clicked.connect(self.choose_image)
        adv.addWidget(self.photo)
        self.wallpapers = Menu("Downloaded wallpapers")
        self.wallpapers.activated.connect(self.pick_wallpaper)
        adv.addWidget(self.wallpapers)
        self.wallpapers.hide()
        self.credit = label("", "muted", True)
        self.credit.setTextFormat(Qt.TextFormat.PlainText)
        adv.addWidget(self.credit)
        self.credit.hide()
        self.font = ToggleSwitch("Use Inter font", "Off keeps the game’s fonts.", False)
        self.font.toggled.connect(lambda on: self.update_theme(font="inter" if on else "inherit"))
        adv.addWidget(self.font)
        body.addWidget(self.advanced)
        self.fine.toggled.connect(self.advanced.setVisible)
        self.fine.toggled.connect(
            lambda on: self.fine.setText("Close fine tuning" if on else "Fine tune")
        )
        self.save_btn = Button("Save theme", "primary", "check")
        self.copy_btn = Button("Save as…")
        self.save_btn.clicked.connect(self.save)
        self.copy_btn.clicked.connect(self.save_as)
        self.samples = QTimer(self)
        self.samples.setSingleShot(True)
        self.samples.timeout.connect(self.update_samples)
        self.fine.toggled.connect(lambda on: self.update_samples() if on else None)
        self.reset(self.theme)

    def set_wallpapers(self, rows: tuple[Media, ...]) -> None:
        self.wallpapers.clear()
        self.wallpapers.addItem("Downloaded wallpapers…")
        for row in rows:
            self.wallpapers.addItem(row.item.title, row.digest)
            origin = row.origin
            self.wallpapers.setItemData(
                self.wallpapers.count() - 1,
                f"{origin.author} · {origin.license}\n{origin.attribution}\n{origin.source_url}",
                Qt.ItemDataRole.ToolTipRole,
            )
        self.wallpapers.setVisible(bool(rows))
        self._select_wallpaper()

    def _select_wallpaper(self) -> None:
        index = max(0, self.wallpapers.findData(self.theme.image))
        self.wallpapers.setCurrentIndex(index)
        self.credit.setVisible(index > 0)
        if index:
            self.credit.setText(self.wallpapers.itemData(index, Qt.ItemDataRole.ToolTipRole))
        self.photo.setText(
            "Change my image…" if self.theme.image and not index else "Use my own image…"
        )

    def pick_wallpaper(self, index: int) -> None:
        if digest := self.wallpapers.itemData(index):
            self.update_theme(wallpaper="image", image=digest)

    def update_samples(self) -> None:
        if self.fine.isChecked() and self.isVisible():
            for style, button in self.styles.items():
                sample = themes.wallpaper(
                    replace(self.theme, wallpaper=style, image=None), (96, 60)
                )
                button.setIcon(QIcon(QPixmap.fromImage(sample)))

    def _swatch(self, key: str, name: str) -> Button:
        button = Button(name)
        button.setStyleSheet("text-align:left;")
        button.clicked.connect(partial(self._edit, key))
        self.swatches[key] = button
        return button

    def _colour(self, key: str, palette: themes.Palette | None = None) -> str:
        if value := getattr(self.theme, key):
            return value
        if key == "your_lp":
            return self.theme.accent
        palette = palette or themes.derive(self.theme)
        if key == "opponent_lp":
            return palette.bars[1].top
        return getattr(palette, key)

    def _edit(self, key: str) -> None:
        self.field = key
        self.mixer.hide()
        self.advanced_mixer.hide()
        mixer = self.mixer if key in BASE else self.advanced_mixer
        # Keep the inline mixer immediately beneath the swatch being edited.
        layout = self.layout() if key in BASE else self.advanced.layout()
        layout.removeWidget(mixer)
        index = layout.indexOf(self.swatches[key]) if key in BASE else list(EXTRA).index(key) + 1
        layout.insertWidget(index + 1, mixer)
        mixer.select((BASE | EXTRA)[key], self._colour(key))

    def _reset(self, key: str) -> None:
        self.advanced_mixer.hide()
        self.update_theme(**{key: None})

    def _number(self, key: str, value: int) -> None:
        self.update_theme(**{key: value})

    def _wall(self, style: str) -> None:
        self.update_theme(wallpaper=style, image=None)

    def update_theme(self, **fields: object) -> None:
        self.set_theme(replace(self.theme, **fields))

    def reset(self, theme: themes.Theme) -> None:
        self.mixer.hide()
        self.advanced_mixer.hide()
        self.fine.setChecked(False)
        self.advanced.hide()
        self.name.setText(theme.name)
        self.set_theme(theme)

    def set_theme(self, theme: themes.Theme) -> None:
        self.theme = theme
        palette = themes.derive(theme)
        for key, button in self.swatches.items():
            value = self._colour(key, palette)
            pix = QPixmap(t.ICON, t.ICON)
            pix.fill(QColor(value))
            button.setIcon(QIcon(pix))
            button.setAccessibleDescription(value)
            button.setToolTip(value.upper())
            if key in self.resets:
                self.resets[key].setEnabled(getattr(theme, key) is not None)
        for key, slider in self.ranges.items():
            with QSignalBlocker(slider):
                slider.setValue(getattr(theme, key))
            self.range_labels[key].setText(f"{slider.accessibleName()}   ·   {slider.value()}")
        for control, on in (
            (self.highlight, theme.highlight_turn),
            (self.font, theme.font == "inter"),
        ):
            with QSignalBlocker(control):
                control.setChecked(on)
        for style, button in self.styles.items():
            button.setText(style.title() + (" ✓" if style == theme.wallpaper else ""))
        if self.fine.isChecked() and self.isVisible():
            self.samples.start(t.MOTION_MS)
        self._select_wallpaper()
        self.inherit.setText(
            "Use game backgrounds" + (" ✓" if theme.wallpaper == "inherit" else "")
        )
        self.changed.emit()

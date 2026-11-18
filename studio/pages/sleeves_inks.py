"""Inline ink colors keep the designer in the Studio window."""

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from studio import theme as t
from studio.widgets import Button, label, margins

from .sleeves_widgets import Slider


class Inks(QWidget):
    changed = Signal()
    editing = Signal(QWidget)

    def __init__(self) -> None:
        super().__init__()
        self.values: tuple[str, ...] = ()
        self.active = -1
        self.buttons = []
        body = QVBoxLayout(self)
        margins(body, 0)
        row = QHBoxLayout()
        row.setSpacing(t.XS)
        body.addLayout(row)
        for i, name in enumerate(("Base", "Shade", "Ink", "Foil")):
            btn = Button(name)
            btn.setCheckable(True)
            btn.setAccessibleName(f"Adjust {name.lower()} color")
            btn.clicked.connect(
                lambda checked=False, index=i: self.choose(index if checked else -1, focus=checked)
            )
            row.addWidget(btn)
            self.buttons.append(btn)
        self.details = QWidget()
        grid = QGridLayout(self.details)
        margins(grid, 0)
        self.sliders = []
        for i, (name, maximum) in enumerate(
            (("Hue", 359), ("Saturation", 255), ("Lightness", 255))
        ):
            slider = Slider(name)
            slider.setRange(0, maximum)
            grid.addWidget(label(name, "small"), i, 0)
            grid.addWidget(slider, i, 1)
            self.sliders.append(slider)
            slider.valueChanged.connect(self.edit)
        body.addWidget(self.details)
        self.details.hide()

    def set_colours(self, colours: tuple[str, ...]) -> None:
        self.values = colours
        for btn, value in zip(self.buttons, colours, strict=True):
            swatch = QPixmap(t.ICON, t.ICON)
            swatch.fill(QColor(value))
            btn.setIcon(QIcon(swatch))
            btn.setToolTip(value)
        self.choose(self.active)

    def choose(self, index: int, *, focus: bool = False) -> None:
        self.active = index
        self.details.setVisible(index >= 0)
        for i, btn in enumerate(self.buttons):
            btn.setChecked(i == index)
        if index >= 0 and self.values:
            hue, sat, light, _ = QColor(self.values[index]).getHsl()
            for slider, value in zip(self.sliders, (hue, sat, light), strict=True):
                with QSignalBlocker(slider):
                    slider.setValue(max(0, value))
            if focus:
                self.sliders[0].setFocus()
                self.editing.emit(self.sliders[0])

    def edit(self) -> None:
        if self.active < 0:
            return
        value = QColor.fromHsl(*(s.value() for s in self.sliders)).name()
        inks = list(self.values)
        inks[self.active] = value
        # Preserve the hue handle even when saturation is zero.
        self.values = tuple(inks)
        swatch = QPixmap(t.ICON, t.ICON)
        swatch.fill(QColor(value))
        btn = self.buttons[self.active]
        btn.setIcon(QIcon(swatch))
        btn.setToolTip(value)
        self.changed.emit()

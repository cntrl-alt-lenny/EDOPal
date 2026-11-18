import random

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QGridLayout, QSlider, QWidget

from sleeves import EMBLEMS, FRAMES, PALETTES, PATTERNS, Emblem, Library, Recipe, render
from studio import theme as t
from studio.tasks import TaskRunner
from studio.widgets import Button, label, margins

from .sleeves_collection import Choice
from .sleeves_editor import Editor
from .sleeves_inks import Inks
from .sleeves_picker import PrintPicker, VisualMenu
from .sleeves_widgets import Menu, Slider


def palette_menu(name: str, *, from_art: bool = False) -> Menu:
    menu = Menu(name)
    if from_art:
        menu.addItem("From the artwork")
    for title, colours in PALETTES.items():
        swatch = QPixmap(t.ICON_BIG, t.LG)
        swatch.fill(QColor(colours[0]))
        from PySide6.QtGui import QPainter

        with QPainter(swatch) as painter:
            for i, value in enumerate(colours):
                painter.fillRect(i * t.ICON_BIG // len(colours), 0, t.SM, t.LG, QColor(value))
        menu.addItem(QIcon(swatch), title)
    return menu


class Designer(Editor):
    def __init__(self, tasks: TaskRunner, library: Library, choice: Choice | None) -> None:
        super().__init__(tasks, "Design a sleeve")
        self.library = library
        self.pattern = VisualMenu("Pattern")
        for key, spec in PATTERNS.items():
            self.pattern.addItem(spec.name, key)
        self.palette = VisualMenu("Palette", PALETTES)
        self.palette.setPlaceholderText("Custom colors")
        self.frame = Menu("Frame")
        for frame in FRAMES:
            self.frame.addItem("Thin border" if frame == "keyline" else frame.title(), frame)
        self.emblem = Menu("Emblem", ("None", *(s.title() for s in EMBLEMS)))
        self.picker = PrintPicker(tasks)
        self.panel.body.insertWidget(0, self.picker, 1)
        self.picker.hide()
        self.picker.chosen.connect(self.pick)
        self.pattern.requested.connect(lambda: self.open_picker(True))
        self.palette.requested.connect(lambda: self.open_picker(False))
        self.controls = QGridLayout()
        self.controls.setHorizontalSpacing(t.MD)
        for i, (title, widget) in enumerate(
            (
                ("Pattern", self.pattern),
                ("Palette", self.palette),
                ("Frame", self.frame),
                ("Emblem", self.emblem),
            )
        ):
            self.controls.addWidget(label(title, "small"), i // 2 * 2, i % 2)
            self.controls.addWidget(widget, i // 2 * 2 + 1, i % 2)
        self.form.addLayout(self.controls)
        self.inks = Inks()
        self.field("Sleeve colors", self.inks)
        self.inks.set_colours(PALETTES[self.palette.currentText()])
        self.inks.changed.connect(self.custom_colours)
        self.inks.editing.connect(self.show_ink_control)
        self.parameters = QWidget()
        self.form.addWidget(self.parameters)
        self.sliders: dict[str, tuple[QSlider, int]] = {}
        self.randomise = Button("Randomize", "secondary", "spark")
        self.tools.addWidget(self.randomise)
        self.randomise.clicked.connect(self.random)
        self.seed: int | None = None
        self.pattern.currentIndexChanged.connect(self.pattern_changed)
        self.palette.currentTextChanged.connect(self.palette_changed)
        for menu in (self.frame, self.emblem):
            menu.currentTextChanged.connect(self.update_preview)
        self.frame.setCurrentIndex(self.frame.findData("keyline"))
        self.pattern.setCurrentIndex(self.pattern.findData("sun"))
        if choice and choice.recipe:
            recipe = choice.recipe
            self.pattern.setCurrentIndex(self.pattern.findData(recipe.pattern))
            self.palette.setCurrentText(choice.palette)
            if recipe.palette and not choice.palette:
                self.inks.set_colours(recipe.palette)
                self.palette.setCurrentIndex(-1)
            self.frame.setCurrentIndex(self.frame.findData(recipe.frame))
            self.emblem.setCurrentText(recipe.emblem.shape.title() if recipe.emblem else "None")
            for key, value in recipe.params.items():
                slider, multiplier = self.sliders[key]
                slider.setValue(round(value * multiplier))
            self.seed = recipe.seed
        self.update_preview()
        self.save.clicked.connect(self.save_design)
        self.initial = self.name.text(), self.recipe()

    def open_picker(self, patterns: bool) -> None:
        self.scroll.hide()
        self.randomise.hide()
        self.picker.present(patterns, self.pattern.currentData(), self.palette.currentText())

    def close_picker(self) -> bool:
        if self.picker.isHidden():
            return False
        self.picker.grid.suspend()
        self.picker.hide()
        self.scroll.show()
        self.randomise.show()
        (self.pattern if self.picker.patterns else self.palette).setFocus()
        return True

    def pick(self, key: str) -> None:
        if self.picker.patterns:
            # Keep the palette the player saw in the pattern tiles.
            palette = self.palette.currentText()
            self.pattern.setCurrentIndex(self.pattern.findData(key))
            if palette:
                self.palette.setCurrentText(palette)
        else:
            self.palette.setCurrentText(key)
        self.close_picker()

    @property
    def dirty(self) -> bool:
        return self.initial != (self.name.text(), self.recipe())

    def show_ink_control(self, slider: QWidget) -> None:
        # Scroll after the newly expanded controls have received their final geometry.
        QTimer.singleShot(0, slider, lambda: self.scroll.ensureWidgetVisible(slider, 0, t.SM))

    def pattern_changed(self) -> None:
        old = self.parameters
        self.parameters = QWidget()
        self.form.replaceWidget(old, self.parameters)
        old.hide()
        old.deleteLater()
        grid = QGridLayout(self.parameters)
        margins(grid, 0)
        grid.setHorizontalSpacing(t.MD)
        grid.setVerticalSpacing(t.SM)
        self.sliders.clear()
        spec = PATTERNS[self.pattern.currentData()]
        for row, param in enumerate(spec.parameters):
            scale = (
                1
                if all(isinstance(v, int) for v in (param.minimum, param.maximum, param.default))
                else 100
            )
            slider = Slider(param.label)
            slider.setRange(round(param.minimum * scale), round(param.maximum * scale))
            slider.setValue(round(param.default * scale))
            value = label(f"{slider.value() / scale:g}", "muted")
            value.setMinimumWidth(t.XXL)
            self.sliders[param.key] = slider, scale
            grid.addWidget(label(param.label, "small"), row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            slider.valueChanged.connect(
                lambda v, text=value, factor=scale: text.setText(f"{v / factor:g}")
            )
            slider.valueChanged.connect(self.update_preview)
        self.palette.setCurrentText(spec.palette)
        self.name.setText(spec.name)
        self.update_preview()

    def recipe(self) -> Recipe:
        emblem = self.emblem.currentText().lower()
        return Recipe(
            self.pattern.currentData(),
            PALETTES.get(self.palette.currentText(), self.inks.values),
            {k: s.value() / mult for k, (s, mult) in self.sliders.items()},
            self.seed,
            self.frame.currentData(),
            None if emblem == "none" else Emblem(emblem),
        )

    def update_preview(self) -> None:
        recipe = self.recipe()
        self.schedule(lambda: render(recipe))

    def palette_changed(self, name: str) -> None:
        if name in PALETTES:
            self.inks.set_colours(PALETTES[name])
        self.update_preview()

    def custom_colours(self) -> None:
        self.palette.setCurrentIndex(-1)
        self.update_preview()

    def random(self) -> None:
        self.pattern.setCurrentIndex(random.randrange(self.pattern.count()))
        self.palette.setCurrentIndex(random.randrange(self.palette.count()))
        self.emblem.setCurrentIndex(random.randrange(self.emblem.count()))
        self.frame.setCurrentIndex(random.randrange(self.frame.count()))
        self.seed = random.randrange(2**31)
        self.update_preview()

    def save_design(self) -> None:
        name, recipe, library = self.name.text(), self.recipe(), self.library
        self.commit.emit(lambda: library.add_design(name, recipe))

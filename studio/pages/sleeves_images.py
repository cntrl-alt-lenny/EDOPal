import asyncio
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem

from sleeves import (
    PALETTES,
    ArtMissing,
    ArtUnavailable,
    Card,
    CardArt,
    CardIndex,
    Layout,
    Library,
    compose,
    decode,
    read_source,
)
from studio import theme as t
from studio.tasks import TaskRunner
from studio.widgets import ActivateOnEnter, Button, ElidedLabel, SearchField, label

from .sleeves_collection import crop_box
from .sleeves_design import palette_menu
from .sleeves_editor import Editor
from .sleeves_widgets import Menu, Slider


class ImageEditor(Editor):
    def __init__(self, tasks: TaskRunner, library: Library, game: Path | None = None) -> None:
        super().__init__(tasks, "From a card" if game else "From an image", scroll_form=False)
        self.library, self.game = library, game
        self.preview.caption = (
            "Choose a card to preview its sleeve"
            if game
            else "Choose a picture to preview its sleeve"
        )
        self.original = b""
        self.source_image = QImage()
        self.dimensions = (1, 1)
        self.centre = (0.5, 0.5)
        self.card: Card | None = None
        self.index: CardIndex | None = None
        self.loading = False
        self.retry_action: Callable[[], None] | None = None
        self.generation = 0
        # The results list owns scrolling. The surrounding image form fits above Save.
        self.form.setAlignment(Qt.AlignmentFlag.AlignTop if not game else Qt.AlignmentFlag(0))
        if game:
            self.search = SearchField("Search card names or numbers")
            self.form.addWidget(self.search)
            self.results = QListWidget()
            ActivateOnEnter(self.results)
            self.results.setAccessibleName("Choose a card to load its artwork")
            self.results.setObjectName("installList")
            self.results.setMinimumHeight(t.BUTTON_H)
            self.form.addWidget(self.results, 1)
            self.search.textChanged.connect(self.find_cards)
            self.results.itemClicked.connect(self.pick_card)
            self.results.itemActivated.connect(self.pick_card)

        else:
            self.pick = Button("Choose image…", "secondary", "folder")
            self.pick.clicked.connect(self.pick_file)
            self.form.addWidget(self.pick)
            self.file = ElidedLabel()
            self.form.addWidget(self.file)
        self.mode = Menu(
            "Image layout",
            ("Framed", "Fill sleeve") if game else ("Fill sleeve", "Fit whole image"),
        )
        self.palette = palette_menu("Frame colors", from_art=True)
        self.background = Menu("Background color")
        unused = self.background if game else self.palette
        unused.setParent(self)
        unused.hide()
        for name, value in (
            ("Midnight", "#142330"),
            ("Charcoal", t.RAIL),
            ("Ivory", t.INK),
            ("Honey", t.GOLD),
            ("Mulberry", PALETTES["Mulberry"][1]),
            ("Jade", PALETTES["Peacock"][1]),
        ):
            pix = QPixmap(t.ICON, t.ICON)
            pix.fill(QColor(value))
            self.background.addItem(QIcon(pix), name, value)
        row = QHBoxLayout()
        row.addWidget(self.mode, 1)
        row.addWidget(self.palette if game else self.background, 1)
        self.form.addWidget(label("Layout and colors", "small"))
        self.form.addLayout(row)
        self.zoom = Slider("Crop zoom")
        self.zoom.setRange(100, 300)
        self.zoom_label = label("Crop zoom", "small")
        self.form.addWidget(self.zoom_label)
        self.form.addWidget(self.zoom)
        self.tip = label("Drag the preview or use its arrow keys to move the crop.", "muted", True)
        self.form.addWidget(self.tip)
        self.retry = Button("Retry")
        self.retry.clicked.connect(lambda: self.retry_action() if self.retry_action else None)
        self.panel.body.insertWidget(self.panel.body.indexOf(self.hint) + 1, self.retry)
        self.retry.hide()
        self.mode.currentTextChanged.connect(self.update_preview)
        self.background.currentTextChanged.connect(self.update_preview)
        self.palette.currentTextChanged.connect(self.update_preview)
        self.zoom.valueChanged.connect(self.update_preview)
        self.preview.moved.connect(self.move_crop)
        self.save.clicked.connect(self.save_image)
        self.update_preview()
        if game:
            self.load_index()
        else:
            self.hint.setText("Choose a still picture to preview it here.")

    def load_index(self) -> None:
        self.hint.setText("Reading cards in this installation…")
        game = self.game
        self.retry.hide()
        job = self.tasks.start(lambda _: CardIndex(game))

        def ready(index: CardIndex) -> None:
            if self.active:
                self.index = index
                self.find_cards()

        job.result.connect(ready)
        job.error.connect(
            lambda _: self.problem(
                "Could not read the card list. Check the game folder and retry.", self.load_index
            )
        )

    def find_cards(self) -> None:
        self.results.clear()
        if self.index is None:
            return
        for card in self.index.search(self.search.text()):
            item = QListWidgetItem(f"{card.name}   ·   {card.passcode}")
            item.setData(Qt.ItemDataRole.UserRole, card)
            self.results.addItem(item)
        if not self.loading and not self.original and not self.retry.isVisible():
            self.preview.caption = (
                "Choose a card to load its artwork."
                if self.results.count()
                else "No matching cards. Try another name or number."
            )
            self.preview.setAccessibleDescription(self.preview.caption)
            self.preview.update()
            self.hint.clear()

    def pick_card(self, item: QListWidgetItem) -> None:
        card = item.data(Qt.ItemDataRole.UserRole)
        if self.loading or (self.card == card and self.original):
            return
        self.results.setCurrentItem(item)
        self.card = card
        self.fetch()

    def fetch(self) -> None:
        card = self.card
        if not card:
            return
        client = CardArt(self.library.root)
        self.load(lambda: asyncio.run(client.fetch(card.passcode)), card.name)

    def pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a sleeve picture", "", "Pictures (*.jpg *.jpeg *.png);;All files (*)"
        )
        if path:
            self.file.set_value(path)
            self.load(lambda: Path(path), Path(path).stem)

    def load(self, get_path: Callable[[], Path], name: str) -> None:
        self.generation += 1
        generation = self.generation
        self.invalidate()
        self.original = b""
        self.source_image = QImage()
        self.preview.set_image(QImage())
        self.loading = True
        self.retry.hide()
        self.name.setText(name)
        self.hint.setText(f"Loading artwork for {name}…" if self.game else "Opening your picture…")
        self.preview.caption = "Loading artwork…" if self.game else "Opening picture…"
        self.update_preview()
        if self.game:
            self.results.setEnabled(False)

        def work() -> tuple[bytes, QImage]:
            data = read_source(get_path())
            image = decode(data)
            return data, image

        def ready(result: tuple[bytes, QImage]) -> None:
            if self.active and generation == self.generation:
                self.original, self.source_image = result
                self.dimensions = self.source_image.size().toTuple()
                self.centre = (0.5, 0.5)
                self.zoom.setValue(100)
                self.hint.setText("")
                self.update_preview()

        def error(exc: Exception) -> None:
            if not self.active or generation != self.generation:
                return
            if isinstance(exc, ArtMissing):
                self.problem("No artwork is available for this card. Choose another card.")
            elif isinstance(exc, ArtUnavailable) or self.game:
                self.problem(
                    "Could not reach the artwork server. Check your connection and retry.",
                    self.fetch,
                )
            elif isinstance(exc, ValueError):
                self.problem(str(exc))
            else:
                self.problem("Could not open this picture. Choose a readable copy.")

        def done() -> None:
            if self.active and generation == self.generation:
                self.loading = False
                if self.game:
                    self.results.setEnabled(True)

        job = self.tasks.start(lambda _: work())
        job.result.connect(ready)
        job.error.connect(error)
        job.finished.connect(done)

    def problem(self, message: str, retry: Callable[[], None] | None = None) -> None:
        if self.active:
            self.hint.setText(message)
            self.preview.caption = "Artwork unavailable" if self.game else "Picture unavailable"
            self.preview.update()
            self.retry_action = retry
            self.retry.setVisible(retry is not None)

    def layout_choice(self) -> Layout:
        mode = {"Fill sleeve": "fill", "Fit whole image": "fit", "Framed": "framed"}[
            self.mode.currentText()
        ]
        crop = (
            crop_box(self.dimensions, self.centre, self.zoom.value() / 100)
            if mode == "fill"
            else None
        )
        return Layout(
            mode, crop, self.background.currentData(), PALETTES.get(self.palette.currentText(), ())
        )

    def update_preview(self) -> None:
        fill = self.mode.currentText() == "Fill sleeve"
        self.preview.set_draggable(fill and bool(self.original))
        self.zoom.setVisible(fill and bool(self.original))
        self.tip.setVisible(fill and bool(self.original))
        self.zoom_label.setVisible(fill and bool(self.original))
        self.palette.setEnabled(not fill)
        self.zoom.setEnabled(bool(self.original))
        if self.original:
            data, layout = self.source_image, self.layout_choice()
            self.schedule(lambda: compose(data, layout))

    def move_crop(self, dx: float, dy: float) -> None:
        if not self.original:
            return
        _, _, cw, ch = self.layout_choice().crop
        w, h = self.dimensions
        self.centre = (
            max(0, min(1, self.centre[0] - dx * cw / max(1, w - cw))),
            max(0, min(1, self.centre[1] - dy * ch / max(1, h - ch))),
        )
        self.update_preview()

    @property
    def dirty(self) -> bool:
        return bool(self.original)

    def leave(self) -> None:
        super().leave()
        self.original = b""
        self.source_image = QImage()

    def save_image(self) -> None:
        library, name, data, layout = (
            self.library,
            self.name.text(),
            self.original,
            self.layout_choice(),
        )
        code = self.card.passcode if self.card else None
        self.commit.emit(lambda: library.add_image(name, data, layout, passcode=code))

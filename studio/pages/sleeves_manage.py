from collections.abc import Callable

from PySide6.QtWidgets import QLineEdit, QSizePolicy

from sleeves import Entry, Library
from studio import theme as t
from studio.tasks import TaskRunner
from studio.widgets import Button, ToggleSwitch

from .sleeves_collection import SOURCES, Choice
from .sleeves_editor import Editor
from .sleeves_widgets import Menu


class Manager(Editor):
    def __init__(
        self,
        tasks: TaskRunner,
        library: Library,
        items: list[Choice],
        removed: list[Choice],
        current: Choice | None,
        confirm: Callable[[str, str], bool],
    ) -> None:
        super().__init__(tasks, "Manage collection")
        self.library = library
        self.confirm = confirm
        self.items = list(items)
        self.removed = removed
        if current and current.key not in {c.key for c in (*items, *removed)}:
            self.items.insert(0, current)
        self.group = Menu("Collection or bin", ["Collection", f"Bin ({len(removed)})"])
        self.tools.addWidget(self.group)
        self.group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.group.setFixedWidth(t.TILE_LARGE)
        self.listing = Menu("Choose a sleeve")
        self.field("Sleeve", self.listing)
        self.tags = QLineEdit()
        self.tags.setAccessibleName("Tags")
        self.tags.setPlaceholderText("Separate tags with commas")
        self.field("Tags", self.tags)
        self.favourite = ToggleSwitch("Favourite", "Show this sleeve in your favourites.", False)
        self.form.addWidget(self.favourite)
        self.remove = Button("Move to bin")
        self.form.addWidget(self.remove)
        self.save.setText("Save changes")
        self.listing.currentIndexChanged.connect(self.selected)
        self.group.currentIndexChanged.connect(self.populate)
        self.save.clicked.connect(self.save_entry)
        self.remove.clicked.connect(self.bin_entry)
        if current in removed:
            self.group.setCurrentIndex(1)
        self.populate()
        if current in self.choices:
            self.listing.setCurrentIndex(self.choices.index(current))

    def populate(self) -> None:
        self.choices = self.removed if self.group.currentIndex() else self.items
        self.listing.clear()
        self.listing.addItems([c.full_name for c in self.choices])
        self.selected()

    def selected(self) -> None:
        from PySide6.QtGui import QImage

        from sleeves import render

        self.invalidate()
        self.preview.set_image(QImage())
        idx = self.listing.currentIndex()
        has = 0 <= idx < len(self.choices)
        in_bin = bool(self.group.currentIndex())
        for widget in (self.name, self.tags, self.favourite):
            widget.setEnabled(has and not in_bin)
        self.remove.setEnabled(has and bool(self.choices[idx].entry))
        self.save.setVisible(not in_bin)
        if not has:
            self.name.clear()
            self.tags.clear()
            self.favourite.setChecked(False)
            self.hint.setText("The bin is empty." if in_bin else "Save a sleeve to manage it here.")
            return
        choice = self.choices[idx]
        entry = choice.entry
        self.name.setText(choice.full_name)
        self.tags.setText(", ".join(entry.tags) if entry else "")
        self.favourite.setChecked(bool(entry and entry.favourite))
        self.remove.setText("Restore to collection" if in_bin else "Move to bin")
        self.save.setText("Save changes" if entry else "Save to collection")
        source = SOURCES[entry.source] if entry else "Ready-made design"
        self.hint.setText(
            f"Source · {source}" + (f" · {entry.passcode}" if entry and entry.passcode else "")
        )
        library = self.library
        if entry:
            self.schedule(lambda: QImage(str(library.image_path(entry.id))))
        else:
            self.schedule(lambda: render(choice.recipe))

    def save_entry(self) -> None:
        choice = self.choices[self.listing.currentIndex()]
        name, tags = self.name.text(), self.tags.text().split(",")
        favourite, library = self.favourite.isChecked(), self.library

        def work() -> Entry:
            entry = choice.entry or library.add_design(name, choice.recipe)
            return library.edit(entry.id, name=name, tags=tags, favourite=favourite)

        self.commit.emit(work)

    def bin_entry(self) -> None:
        choice = self.choices[self.listing.currentIndex()]
        in_bin = not self.group.currentIndex()
        if in_bin and not self.confirm(
            "Move sleeve to bin?",
            f"Remove {choice.name} from your collection? You can restore it from the bin. Your game keeps its current sleeves.",
        ):
            return
        library = self.library
        self.commit.emit(lambda: library.set_in_bin(choice.entry.id, bool(in_bin)))

"""The in-page list picker uses the same grouped, keyboard-operated list as Formats."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from practice.library import Library
from studio import theme as t
from studio.pages.formats_views import DATA, CatalogueList, era_name
from studio.pages.practice_widgets import across
from studio.widgets import Button, SearchField, label, margins


class ListPicker(QWidget):
    back_requested = Signal()
    rescan_requested = Signal()
    chosen = Signal(int)

    def __init__(self, library: Library, selected: int | None) -> None:
        super().__init__()
        self.library = library
        self.selected = selected
        layout = QVBoxLayout(self)
        margins(layout, 0)
        layout.setSpacing(t.MD)
        self.back = Button("Back", "quiet", "back")
        self.back.clicked.connect(self.back_requested)
        heading = across(self.back, label("Choose a list or format", "heading"))
        heading.addStretch()
        layout.addLayout(heading)
        layout.addWidget(
            label(
                "Check the opponents in your game against an installed list or a bundled card pool.",
                "muted",
                True,
            )
        )
        self.search = SearchField()
        self.search.setPlaceholderText("Search lists, formats, regions or eras")
        self.search.setAccessibleName(self.search.placeholderText())
        layout.addWidget(self.search)
        self.list = CatalogueList()
        self.list.setAccessibleName("Installed lists and bundled formats")
        layout.addWidget(self.list, 1)
        self.message = label("", "body", True)
        layout.addWidget(self.message)
        self.use = Button("Check opponents", "primary", "check")
        self.use.clicked.connect(self.activate)
        self.list.itemActivated.connect(lambda _: self.activate())
        layout.addWidget(self.use, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(0)
        self.search.textChanged.connect(self.fill)
        self.fill()

    def fill(self) -> None:
        query = self.search.text().casefold().split()
        rows = [
            dict(id=-1 - i, name=name, era="Installed in EDOPro", date="On disk")
            for i, name in enumerate(self.library.opponents.lists)
        ]
        catalogue = self.library.catalogue
        if catalogue:
            rows.extend(
                dict(row, era=f"Bundled · {region} / {era_name(row['era'])}")
                for region in ("TCG", "OCG")
                for row in catalogue.select(region)
                if "file" in row
            )
        rows = [
            r
            for r in rows
            if all(
                w
                in " ".join((r["name"], r["era"], r["date"] or "", r.get("banlist", ""))).casefold()
                for w in query
            )
        ]
        self.list.fill(rows, {}, self.selected)
        self.list.setVisible(bool(rows))
        self.use.setText("Check opponents" if rows else "Clear search" if query else "Rescan")
        self.message.setText(
            "No lists or formats match your search."
            if not rows and query
            else "The bundled formats could not be read."
            if catalogue is None
            else ""
        )
        self.message.setVisible(bool(self.message.text()))
        self.layout().setStretch(self.layout().count() - 1, 0 if rows else 1)

    def activate(self) -> None:
        item = self.list.currentItem()
        if item and (row := item.data(DATA)):
            self.chosen.emit(row["id"])
        elif self.search.text():
            self.search.clear()
            self.search.setFocus()
        else:
            self.rescan_requested.emit()

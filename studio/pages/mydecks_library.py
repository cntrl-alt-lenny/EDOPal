"""Search, sort and open decks from a virtualized collection list."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QModelIndex, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from decks import Library, SavedDeck
from decks.duplicates import DuplicateIndex
from studio import theme as t
from studio.pages.mydecks_logic import library_rows, quantity
from studio.pages.mydecks_widgets import DeckDelegate, DeckHero, RowModel, Screen, button, choice
from studio.widgets import SearchField, label


class Collection(Screen):
    def __init__(
        self,
        lib: Library,
        open_deck: Callable[[SavedDeck], None],
        check: Callable,
        compare: Callable,
        import_code: Callable,
        refresh: Callable,
        duplicates: DuplicateIndex,
        show_duplicates: Callable[[], None],
        missing: int,
        show_missing: Callable[[], None],
    ) -> None:
        super().__init__()
        self.body.setContentsMargins(t.XXL, t.MD, t.XXL, t.MD)
        self.body.setSpacing(t.SM)
        self.lib, self.open_deck = lib, open_deck
        self.marks = duplicates.marks
        self.folders = sorted({d.folder for d in lib.decks}, key=lambda p: p.as_posix().casefold())
        summary = f"{quantity(len(lib.decks), 'deck')}   ·   {quantity(len(self.folders), 'folder')}   ·   {quantity(len(lib.banlists), 'installed list')}"
        self.hero = DeckHero(summary)
        self.body.addWidget(self.hero)
        panel = self.panel(t.MD)
        panel.body.setSpacing(t.SM)
        top = QHBoxLayout()
        self.summary = label("", "subheading")
        top.addWidget(self.summary)
        if lib.issues:
            self.issue_btn = button("Scan details", self.show_issues, "quiet", "warning")
            self.issue_btn.setToolTip(quantity(len(lib.issues), "scan warning"))
            top.addWidget(self.issue_btn)
        top.addStretch()
        self.check = button("Check format", check, symbol="check")
        self.compare = button("Compare", compare, symbol="deck")
        self.import_btn = button("Import code", import_code, "primary", "download")
        self.compare.setEnabled(sum(d.preview is not None for d in lib.decks) >= 2)
        self.check.setEnabled(bool(lib.decks))
        for btn in (self.check, self.compare, self.import_btn):
            top.addWidget(btn)
        panel.body.addLayout(top)
        filters = QHBoxLayout()
        self.mode = choice("Search by", ["Deck name", "Card name"])
        self.search = SearchField("Search deck names")
        self.folder = choice(
            "Filter by folder",
            [
                "All folders",
                *("Deck folder" if p == Path(".") else p.as_posix() for p in self.folders),
            ],
        )
        self.sort = choice("Sort decks", ["Name A–Z", "Last changed", "Most cards"])
        for w, stretch in ((self.mode, 2), (self.search, 5), (self.folder, 2), (self.sort, 2)):
            filters.addWidget(w, stretch)
        panel.body.addLayout(filters)
        self.rows = QListView()
        self.rows.setAccessibleName("Deck library. Open a deck to see its cards.")
        self.rows.setUniformItemSizes(True)
        self.rows.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.rows.setMouseTracking(True)
        self.rows.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows.setStyleSheet(
            f"QListView {{ background:{t.PANEL}; border: {t.HAIR}px solid {t.EDGE}; padding: 0; }}"
            f"QListView:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}"
        )
        self.model = RowModel(self.rows)
        self.rows.setModel(self.model)
        self.rows.setItemDelegate(DeckDelegate(self.rows))
        self.rows.clicked.connect(self.open)
        self.rows.activated.connect(self.open)
        panel.body.addWidget(self.rows, 1)
        self.empty_view = QWidget()
        empty_layout = QVBoxLayout(self.empty_view)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        self.empty = label("No decks match", "subheading", True)
        empty_layout.addWidget(self.empty)
        self.clear_filters = button("Clear search and filters", self.reset_filters, "primary")
        empty_layout.addWidget(self.clear_filters, 0, Qt.AlignmentFlag.AlignLeft)
        empty_layout.addStretch()
        panel.body.addWidget(self.empty_view, 1)
        footer = QHBoxLayout()
        self.legend = QWidget()
        legend_items = QHBoxLayout(self.legend)
        legend_items.setContentsMargins(0, 0, 0, 0)
        if lib.decks:
            for word, colour in (("Monster", t.GOLD), ("Spell", t.GREEN), ("Trap", t.RED)):
                swatch = QWidget()
                swatch.setFixedSize(t.MD, t.XS)
                swatch.setStyleSheet(f"background: {colour};")
                legend_items.addWidget(swatch)
                legend_items.addWidget(label(word, "muted"))
        footer.addWidget(self.legend)
        footer.addStretch()
        self.duplicates = button("Duplicates", show_duplicates, "quiet", "deck")
        self.duplicates.setEnabled(bool(lib.decks))
        footer.addWidget(self.duplicates)
        if missing:
            self.missing = button(f"Missing decks ({missing})", show_missing, "quiet")
            footer.addWidget(self.missing)
        self.refresh = button("Refresh", refresh, "quiet")
        footer.addWidget(self.refresh)
        panel.body.addLayout(footer)
        self.warning = QListWidget()
        self.warning.setAccessibleName("Scan warnings")
        self.warning.setObjectName("installList")
        for issue in lib.issues:
            message = issue.replace(str(lib.game) + os.sep, "")
            item = QListWidgetItem(
                message.replace("Missing banlist header", "This file has no forbidden list name")
            )
            item.setToolTip(issue)
            self.warning.addItem(item)
        self.warning.hide()
        panel.body.insertWidget(panel.body.indexOf(self.rows) + 1, self.warning, 1)
        self.search.textChanged.connect(self.filter)
        for box in (self.mode, self.sort, self.folder):
            box.currentIndexChanged.connect(self.filter)
        self.search.returnPressed.connect(self.first_result)
        self.initial = self.search if lib.decks else self.import_btn
        self.filter()
        if not lib.decks:
            self.hero.headline.setText("Your first deck")
            self.hero.description.setText(
                "Import a shared deck or build one in EDOPro’s Deck Edit."
            )
            self.body.setAlignment(panel, Qt.AlignmentFlag.AlignTop)
            panel.body.insertWidget(0, self.import_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.summary.hide()
            for control in (
                self.mode,
                self.search,
                self.folder,
                self.sort,
                self.check,
                self.compare,
                self.duplicates,
            ):
                control.hide()

    def show_issues(self) -> None:
        showing = self.warning.isHidden()
        self.warning.setVisible(showing)
        self.issue_btn.setText("Back to decks" if showing else "Scan details")
        self.show_results()
        (self.warning if showing else self.search).setFocus()

    def reset_filters(self) -> None:
        with QSignalBlocker(self.search), QSignalBlocker(self.mode), QSignalBlocker(self.folder):
            self.mode.setCurrentIndex(0)
            self.folder.setCurrentIndex(0)
            self.search.clear()
        self.filter()
        self.search.setFocus()

    def show_results(self) -> None:
        showing_decks = self.warning.isHidden()
        rows = bool(self.model.rows)
        has_matches = showing_decks and rows
        no_matches = showing_decks and not rows and bool(self.lib.decks)
        self.rows.setVisible(has_matches)
        self.empty_view.setVisible(no_matches)
        self.legend.setVisible(has_matches)
        role = "secondary" if no_matches else "primary"
        if self.import_btn.property("role") != role:
            self.import_btn.setProperty("role", role)
            self.import_btn.style().unpolish(self.import_btn)
            self.import_btn.style().polish(self.import_btn)

    def first_result(self) -> None:
        if self.model.rows:
            self.rows.setCurrentIndex(self.model.index(0))
            self.rows.setFocus()

    def select_saved(self, paths: tuple[Path, ...]) -> int:
        targets = set(paths)
        matches = [
            self.model.index(i) for i, row in enumerate(self.model.rows) if row.deck.path in targets
        ]
        if matches:
            flags = QItemSelectionModel.SelectionFlag
            selection = QItemSelection()
            for index in matches:
                selection.select(index, index)
            model = self.rows.selectionModel()
            model.select(selection, flags.ClearAndSelect)
            model.setCurrentIndex(matches[0], flags.NoUpdate)
            self.rows.scrollTo(matches[0], QListView.ScrollHint.PositionAtTop)
            self.rows.setFocus()
        return len(matches)

    def open(self, index: QModelIndex) -> None:
        if self.isVisible() and index.isValid():
            self.open_deck(self.model.rows[index.row()].deck)

    def filter(self) -> None:
        by_card = self.mode.currentIndex() == 1
        self.search.setPlaceholderText("Search for a card" if by_card else "Search deck names")
        self.search.setAccessibleName(self.search.placeholderText())
        folder = (
            self.folders[self.folder.currentIndex() - 1] if self.folder.currentIndex() > 0 else None
        )
        rows = library_rows(
            self.lib, self.search.text(), by_card, folder, self.sort.currentIndex(), self.marks
        )
        self.model.replace(rows)
        self.summary.setText(quantity(len(rows), "deck"))
        counts = "Matching copies by section" if by_card and self.search.text().strip() else ""
        self.summary.setToolTip(counts)
        self.rows.setAccessibleDescription(counts)
        self.show_results()

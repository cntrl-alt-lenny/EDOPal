"""Review identical and similar deck files as groups."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout

from decks import SavedDeck
from decks.duplicates import NEAR_EDITS, DuplicateIndex, difference
from studio import theme as t
from studio.pages.mydecks_checks import ReadingPane, comparison_html
from studio.pages.mydecks_logic import deck_title, quantity
from studio.pages.mydecks_widgets import Screen, button, choice
from studio.tasks import Job, TaskRunner
from studio.widgets import SearchField, label


class DuplicatesScreen(Screen):
    def __init__(
        self,
        duplicates: DuplicateIndex,
        runner: TaskRunner,
        back: Callable[[], object],
        open_deck: Callable[[SavedDeck], None],
        reveal: Callable[[Path], None],
        selected: SavedDeck | None = None,
    ) -> None:
        super().__init__("Duplicate decks", back)
        self.duplicates, self.runner = duplicates, runner
        self.members: tuple[SavedDeck, ...] = ()
        self.job: Job | None = None
        self.generation = 0
        if not duplicates.groups:
            self.body.addWidget(label("No identical or similar decks", "subheading", True))
            self.body.addWidget(
                label(
                    f"Similar decks are up to {NEAR_EDITS} card changes apart. Refresh after saving new copies in EDOPro.",
                    "body",
                    True,
                )
            )
            self.body.addStretch()
            return
        panel = self.panel()
        filters = QHBoxLayout()
        filters.addWidget(label("Group", "subheading"))
        self.group = choice(
            "Deck group",
            (f"{group[0].name}  ·  {quantity(len(group), 'deck')}" for group in duplicates.groups),
        )
        filters.addWidget(self.group, 1)
        panel.body.addLayout(filters)
        work = QHBoxLayout()
        left = QVBoxLayout()
        self.search = SearchField("Find files in this group")
        left.addWidget(self.search)
        self.files = QListWidget()
        self.files.setObjectName("installList")
        self.files.setAccessibleName("Deck files in the group")
        self.files.setMinimumHeight(t.TILE_H)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.addWidget(self.files, 1)
        self.count = label("", "muted")
        left.addWidget(self.count)
        work.addLayout(left, 2)
        right = QVBoxLayout()
        right.addWidget(label("Compare against", "subheading"))
        self.base = choice("Compare against a deck", ())
        right.addWidget(self.base)
        self.summary = label("", "body", True)
        right.addWidget(self.summary)
        self.report = ReadingPane("Card differences between the two selected deck files")
        right.addWidget(self.report, 1)
        work.addLayout(right, 3)
        panel.body.addLayout(work, 1)
        panel.body.addWidget(
            label(
                f"Similar decks differ by up to {NEAR_EDITS} moves, replacements, additions or removals. Groups can link decks through a close match.",
                "muted",
                True,
            )
        )
        actions = QHBoxLayout()
        actions.addStretch()
        self.open = button("Open deck", lambda: open_deck(self.members[self.files.currentRow()]))
        self.folder = button(
            "Open folder",
            lambda: reveal(self.members[self.files.currentRow()].path),
            "primary",
            "folder",
        )
        actions.addWidget(self.open)
        actions.addWidget(self.folder)
        self.body.addLayout(actions)
        self.files.itemActivated.connect(lambda _: self.open.click())
        self.files.currentRowChanged.connect(self._compare)
        self.search.textChanged.connect(self._filter)
        self.base.currentIndexChanged.connect(self._compare)
        self.group.setCurrentIndex(duplicates.by_path.get(selected.path, 0) if selected else 0)
        self.group.currentIndexChanged.connect(self._group)
        self._group(self.group.currentIndex(), selected)
        self.initial = self.files

    def leave(self) -> None:
        self.generation += 1
        if self.job is not None:
            self.job.cancel()

    def _group(self, index: int, selected: SavedDeck | None = None) -> None:
        self.members = self.duplicates.groups[index]
        with QSignalBlocker(self.files), QSignalBlocker(self.base):
            self.files.clear()
            self.base.clear()
            for deck in self.members:
                title = deck_title(deck)
                mark = self.duplicates.marks[deck.path]
                item = QListWidgetItem(f"{title}   ·   {mark}")
                item.setToolTip(f"{deck.path}\n{mark}")
                self.files.addItem(item)
                self.base.addItem(title)
            row = self.members.index(selected) if selected in self.members else 1
            self.files.setCurrentRow(row)
            self.base.setCurrentIndex(1 if row == 0 else 0)
        self._filter()

    def _filter(self) -> None:
        term = self.search.text().strip().casefold()
        visible = []
        for i, deck in enumerate(self.members):
            item = self.files.item(i)
            match = term in deck_title(deck).casefold()
            item.setHidden(not match)
            if match:
                visible.append(i)
        self.count.setText(f"{len(visible)} of {len(self.members)} files")
        if self.files.currentRow() not in visible:
            self.files.setCurrentRow(visible[0] if visible else -1)
        else:
            self._compare()

    def _compare(self) -> None:
        row, base = self.files.currentRow(), self.base.currentIndex()
        self.leave()
        generation = self.generation
        for action in (self.open, self.folder):
            action.setEnabled(row >= 0)
        if row < 0 or base < 0:
            self.summary.setText("No deck files match this search.")
            self.report.clear()
            return
        if row == base:
            self.summary.setText("Choose a different deck to compare these files.")
            self.report.clear()
            return
        before, after = self.members[base].preview, self.members[row].preview
        assert before is not None and after is not None
        self.summary.setText("Comparing decks…")
        self.report.clear()
        self.job = self.runner.start(
            lambda _: (
                difference(before.sections, after.sections),
                comparison_html(before, after, separate_counts=True),
            )
        )

        def ready(result: tuple[int, str]) -> None:
            if generation == self.generation:
                distance, report = result
                self.summary.setText(
                    "Identical cards and sections"
                    if not distance
                    else (
                        f"{quantity(distance, 'card change')} · Similar"
                        if distance <= NEAR_EDITS
                        else f"{quantity(distance, 'card change')} · Linked through another deck"
                    )
                )
                self.report.setHtml(report)

        self.job.result.connect(ready)
        self.job.error.connect(
            lambda _: (
                self.summary.setText("Couldn’t compare these decks.")
                if generation == self.generation
                else None
            )
        )

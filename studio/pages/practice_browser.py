from __future__ import annotations

from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from practice.bots import FIT, Bot, filter_bots, random_opponent, rule_name
from practice.library import Library
from practice.matchups import Matchups
from practice.progress import STATES, Progress, filter_puzzles, next_unsolved
from practice.puzzles import Puzzle
from studio import theme as t
from studio.pages.decks_contents import DeckContents
from studio.pages.decks_preview_data import PreviewSection
from studio.pages.mydecks_logic import saved_sections
from studio.pages.mydecks_widgets import choice
from studio.pages.practice_detail import rules
from studio.pages.practice_widgets import PracticeList, across, column, scroll
from studio.tasks import TaskRunner
from studio.widgets import Button, ElidedLabel, Panel, SearchField, label, margins

COLOURS = {"Fits": t.GREEN, "Does not fit": t.RED, "Solved": t.GREEN, "Attempted": t.GOLD}


def options(box: QComboBox, values: list[tuple[str, object]], index: int = 0) -> None:
    box.blockSignals(True)
    box.clear()
    for title, data in values:
        box.addItem(title, data)
    box.setCurrentIndex(index)
    box.blockSignals(False)
    box.setToolTip(box.currentText())


class Browser(QWidget):
    open_requested = Signal()
    refresh_requested = Signal()
    lists_requested = Signal()
    formats_requested = Signal()

    def __init__(
        self,
        game: Path,
        library: Library,
        progress: dict[str, Progress],
        runner: TaskRunner,
        *,
        progress_available: bool = True,
    ) -> None:
        super().__init__()
        self.game, self.library, self.progress, self.runner = game, library, progress, runner
        self.progress_available = progress_available
        self.puzzle_mode = False
        self.matches: Matchups | None = None
        self.list_index = 0 if library.opponents.lists else -1
        self.list_name = next(iter(library.opponents.lists), "")
        self.format_id: int | None = None
        self.last_pick: Bot | None = None
        self.rows: list[Bot] | list[Puzzle] = []
        self.sections: dict[str, tuple[PreviewSection, ...]] = {}
        self.current: Bot | Puzzle | None = None
        self.preview_job = None
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.MD)
        self.bots = Button(f"Opponents  ·  {len(library.opponents.bots)}", symbol="practice")
        self.puzzles = Button(f"Puzzles  ·  {len(library.puzzles)}", symbol="library")
        for button in (self.bots, self.puzzles):
            button.setCheckable(True)
            button.setStyleSheet(
                f"QPushButton:checked {{background: {t.ACTIVE}; color: {t.GOLD}; border-color: {t.GOLD};}}"
            )
        self.bots.clicked.connect(lambda: self.mode(False))
        self.puzzles.clicked.connect(lambda: self.mode(True))
        row = across(self.bots, self.puzzles)
        row.addStretch()
        self.show_filters = Button("Filters")
        self.show_filters.setCheckable(True)
        self.show_filters.toggled.connect(self._size_filters)
        row.addWidget(self.show_filters)
        self.pick = Button("Random opponent", symbol="practice")
        self.pick.clicked.connect(self.pick_row)
        row.addWidget(self.pick)
        self.refresh = Button("Rescan", "quiet")
        self.refresh.clicked.connect(self.refresh_requested)
        row.addWidget(self.refresh)
        body.addLayout(row)
        self.search = SearchField()
        self.list_choice = Button("", symbol="library")
        self.list_choice.clicked.connect(self.lists_requested)
        self.open_formats = Button("Open Formats", "quiet", "arrow")
        self.open_formats.clicked.connect(self.formats_requested)
        self.rule = choice("Master Rule", [])
        self.level = choice("Difficulty", [])
        self.fit = choice("List fit", [])
        self.collection = choice("Puzzle collection", [])
        self.progress_filter = choice("Progress filter", [])
        self.progress_filter.setEnabled(progress_available)
        bots = library.opponents.bots
        options(
            self.rule,
            [("All rules", None)]
            + [(rule_name(r), r) for r in sorted({r for b in bots for r in b.rules})],
        )
        options(
            self.level,
            [("All levels", "")]
            + [(f"Level {d}", d) for d in sorted({b.difficulty for b in bots})],
        )
        options(self.fit, [("Any list fit", "")] + [(s, s) for s in FIT.values()])
        options(self.progress_filter, [("Any progress", "")] + [(s, s) for s in STATES])
        self.update_counts()
        body.addLayout(
            across(
                self.search,
                self.list_choice,
                self.open_formats,
                self.collection,
                self.progress_filter,
            )
        )
        self.filters = QWidget()
        filters = across(self.rule, self.level, self.fit)
        margins(filters, 0)
        self.filters.setLayout(filters)
        body.addWidget(self.filters)
        self.warning = label("", "body", True)
        body.addWidget(self.warning)
        self.results = PracticeList()
        self.results.selectionModel().currentChanged.connect(self.select)
        self.results.activated.connect(lambda _: self.open_requested.emit())
        self.total = label("", "goldKicker")
        left, column_layout = column()
        column_layout.addWidget(self.total)
        column_layout.addWidget(self.results, 1)
        self.preview = Panel(t.LG)
        self.kicker = label("", "goldKicker")
        self.name = ElidedLabel()
        self.name.setProperty("role", "heading")
        self.description = label("", "body", True)
        self.preview.body.addWidget(self.kicker)
        self.preview.body.addWidget(self.name)
        self.preview.body.addWidget(self.description)
        self.inner = QStackedWidget()
        self.deck = DeckContents(runner, inset=0)
        self.inner.addWidget(self.deck)
        self.fact = Panel(t.MD)
        self.fact_title = label("", "goldKicker")
        self.fact_copy = label("", "body", True)
        self.fact_detail = label("", "muted", True)
        for item in (self.fact_title, self.fact_copy, self.fact_detail):
            self.fact.body.addWidget(item)
        self.life_box = QWidget()
        life_row = QHBoxLayout(self.life_box)
        margins(life_row, 0)
        self.life_numbers = []
        for caption in ("YOUR STARTING LP", "OPPONENT’S LP"):
            stat = Panel(t.MD)
            stat.setStyleSheet(f"QFrame[role='panel'] {{ background: {t.RAISED}; }}")
            number = label("", "metric")
            stat.body.addWidget(label(caption, "goldKicker"))
            stat.body.addWidget(number)
            life_row.addWidget(stat)
            self.life_numbers.append(number)
        self.fact.body.addWidget(self.life_box)
        self.collection_count = label("", "body", True)
        self.fact.body.addWidget(self.collection_count)
        self.fact.body.addStretch()
        self.inner.addWidget(scroll(self.fact))
        self.preview.body.addWidget(self.inner, 1)
        self.open = Button("Matchup guide", "primary", "arrow")
        self.open.clicked.connect(self.open_requested)
        self.preview.body.addWidget(self.open, 0, Qt.AlignmentFlag.AlignLeft)
        self.content = QWidget()
        split = QHBoxLayout(self.content)
        margins(split, 0)
        split.setSpacing(t.LG)
        split.addWidget(left, 4)
        split.addWidget(self.preview, 6)
        body.addWidget(self.content, 1)
        self.empty = Panel(t.XL)
        self.empty_title = label("", "heading", True)
        self.empty_copy = label("", "body", True)
        self.empty.body.addWidget(self.empty_title)
        self.empty.body.addWidget(self.empty_copy)
        self.clear = Button("Clear filters", "primary")
        self.clear.clicked.connect(self._recover)
        self.empty.body.addWidget(self.clear, 0, Qt.AlignmentFlag.AlignLeft)
        self.empty.body.addStretch()
        body.addWidget(self.empty, 1)
        self.search.textChanged.connect(self.apply)
        for box in (
            self.rule,
            self.level,
            self.fit,
            self.collection,
            self.progress_filter,
        ):
            box.currentIndexChanged.connect(self.apply)
        self.mode(False)

    def _size_filters(self) -> None:
        compact = self.window().height() <= t.MIN_SIZE[1] + t.HUGE
        has_bots = bool(self.library.opponents.bots)
        self.show_filters.setVisible(has_bots and compact and not self.puzzle_mode)
        self.filters.setVisible(
            has_bots and not self.puzzle_mode and (not compact or self.show_filters.isChecked())
        )
        prefix = "Format" if self.matches else "List or format"
        title = f"{prefix} · {self.list_name}" if self.list_name else "Choose list or format"
        self.list_choice.setToolTip(f"Choose list or format · {title}")
        self.list_choice.setAccessibleName(self.list_choice.toolTip())
        self.list_choice.setText(
            self.fontMetrics().elidedText(
                title, Qt.TextElideMode.ElideRight, max(t.TILE_H, self.width() // 3)
            )
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._size_filters()

    def update_counts(self) -> None:
        totals = Counter(p.collection for p in self.library.puzzles)
        solved = Counter(
            p.collection
            for p in self.library.puzzles
            if self.progress.get(p.path, Progress()).status == "Solved"
        )
        self.counts = {
            c: f"{solved[c]} of {n} solved in this collection"
            if self.progress_available
            else "Progress unavailable"
            for c, n in totals.items()
        }
        entries = [("All collections", "")] + [
            (
                c.replace("/", " › ")
                + (f"   ·   {solved[c]} of {n} solved" if self.progress_available else ""),
                c,
            )
            for c, n in totals.items()
        ]
        options(self.collection, entries, max(0, self.collection.currentIndex()))

    def mode(self, puzzles: bool) -> None:
        changed = self.puzzle_mode != puzzles
        self.puzzle_mode = puzzles
        has_entries = bool(self.library.puzzles if puzzles else self.library.opponents.bots)
        self.search.setVisible(has_entries)
        self.pick.setVisible(has_entries)
        self.bots.setChecked(not puzzles)
        self.puzzles.setChecked(puzzles)
        for box in (self.list_choice, self.rule, self.level, self.fit):
            box.setVisible(not puzzles and has_entries)
        self.collection.setVisible(puzzles and has_entries)
        self.progress_filter.setVisible(puzzles and has_entries)
        self.open_formats.setVisible(not puzzles and self.matches is not None)
        self._size_filters()
        self.search.setPlaceholderText(
            "Search title or objective" if puzzles else "Search opponent or card"
        )
        self.search.setAccessibleName(self.search.placeholderText())
        if changed:
            self.search.clear()
        self.pick.setText("Next unsolved" if puzzles else "Random opponent")
        self.open.setText("Open puzzle" if puzzles else "Matchup guide")
        self.apply()

    def reset_filters(self) -> None:
        self.search.clear()
        for box in (self.rule, self.level, self.fit, self.progress_filter, self.collection):
            box.setCurrentIndex(0)

    def _recover(self) -> None:
        entries = self.library.puzzles if self.puzzle_mode else self.library.opponents.bots
        if entries:
            self.reset_filters()
        else:
            self.refresh_requested.emit()

    def use_list(
        self, index: int, name: str, fid: int | None = None, matches: Matchups | None = None
    ) -> None:
        self.list_index, self.list_name, self.format_id, self.matches = index, name, fid, matches
        self.current = None
        self.mode(False)

    def apply(self) -> None:
        self.last_pick = None
        self.pick.setText("Next unsolved" if self.puzzle_mode else "Random opponent")
        old = self.current
        lib = self.library
        if self.puzzle_mode:
            self.rows = filter_puzzles(
                lib.puzzles,
                self.progress,
                self.search.text(),
                self.collection.currentData(),
                self.progress_filter.currentData(),
            )
            statuses = [
                self.progress.get(p.path, Progress()).status
                if self.progress_available
                else "Progress unavailable"
                for p in self.rows
            ]
            values = [
                (p.title, f"{s}   ·   {p.collection.rsplit('/', 1)[-1]}", COLOURS.get(s, t.MUTED))
                for p, s in zip(self.rows, statuses, strict=True)
            ]
            count = len(self.rows)
            total = f"{count} {'PUZZLE' if count == 1 else 'PUZZLES'}"
            self.total.setText(
                total
                + (f"   ·   {statuses.count('Solved')} SOLVED" if self.progress_available else "")
            )
            self.pick.setEnabled(self.progress_available and any(s != "Solved" for s in statuses))
            warning = lib.puzzle_warning
            has_library = bool(lib.puzzles)
            noun = "puzzles"
        else:
            self.rows = filter_bots(
                self.matches.bots if self.matches else lib.opponents.bots,
                self.search.text(),
                self.rule.currentData(),
                self.level.currentData(),
                self.fit.currentData(),
                self.list_index,
            )
            values = []
            for b in self.rows:
                fit = b.fit(self.list_index)
                subtitle = f"{fit}    ·    Level {b.difficulty}    ·    {rules(b)}"
                if self.matches:
                    subtitle = (
                        f"{b.distance.outside} outside · {b.distance.excess} over limit"
                        if b.distance and fit == "Does not fit"
                        else fit
                    )
                values.append(
                    (
                        b.name,
                        subtitle,
                        COLOURS.get(fit, t.MUTED),
                    )
                )
            self.pick.setEnabled(bool(values))
            self.total.setText(f"{len(values)} {'OPPONENT' if len(values) == 1 else 'OPPONENTS'}")
            warning = " ".join(
                filter(
                    None,
                    (
                        self.matches.summary if self.matches else "",
                        lib.opponents.warning,
                        lib.opponents.runtime,
                    ),
                )
            )
            has_library = bool(lib.opponents.bots)
            noun = "opponents"
        self.warning.setText(warning)
        self.warning.setVisible(bool(warning) and has_library)
        self.content.setVisible(bool(values))
        self.empty.setVisible(not values)
        self.empty_title.setText(f"No matching {noun}" if has_library else f"No {noun} to browse")
        self.empty_copy.setText(
            "Try a different search or clear the filters."
            if has_library
            else warning or f"The selected game folder contains no {noun}."
        )
        self.clear.setText("Clear filters" if has_library else "Rescan")
        self.refresh.setVisible(has_library)
        self.results.rows.replace(values)
        if values:
            row = self.rows.index(old) if old in self.rows else 0
            self.results.setCurrentIndex(self.results.rows.index(row, 0))
        else:
            self.current = None

    def select(self) -> None:
        index = self.results.currentIndex().row()
        if not 0 <= index < len(self.rows):
            return
        entry = self.current = self.rows[index]
        self.life_box.setVisible(isinstance(entry, Puzzle))
        self.collection_count.setVisible(isinstance(entry, Puzzle))
        if self.preview_job:
            self.preview_job.cancel()
            self.preview_job = None
        if isinstance(entry, Bot):
            self.name.set_value(entry.name)
            self.kicker.setText(entry.fit(self.list_index).upper())
            self.description.setText(
                f"Deck · {entry.deck or 'Not stated'}    ·    Level {entry.difficulty}    ·    {rules(entry)}"
            )
            self.inner.setCurrentIndex(0 if entry.preview else 1)
            if entry.preview:
                self.deck.set_sections(self.sections.get(entry.file, ()))
                if entry.file not in self.sections:
                    game = self.game
                    job = self.preview_job = self.runner.start(
                        lambda w: saved_sections(game, entry.preview, w.cancel), owner=self
                    )

                    def done(sections: tuple[PreviewSection, ...]) -> None:
                        if sections and self.current is entry:
                            self.sections[entry.file] = sections
                            self.deck.set_sections(sections)

                    job.result.connect(done)
                return
            self.fact_title.setText("OPPONENT DECK")
            self.fact_copy.setText(entry.problem)
            self.fact_detail.setText(f"Listed deck · {entry.deck or 'Not stated'}")
        else:
            self.inner.setCurrentIndex(1)
            self.open.setText("Open journal" if entry.error else "Open puzzle")
            self.kicker.setText(
                self.progress.get(entry.path, Progress()).status.upper()
                if self.progress_available
                else "PROGRESS UNAVAILABLE"
            )
            self.name.set_value(entry.title)
            self.description.setText(entry.collection.replace("/", " › "))
            self.fact_title.setText(
                "FROM THE STARTING SETUP" if entry.setup and not entry.objective else "OBJECTIVE"
            )
            self.fact_copy.setText(entry.error or entry.objective or entry.setup or "Not stated")
            self.fact_detail.setText(f"Difficulty   ·   {entry.difficulty or 'Not stated'}")
            for field, value in zip(self.life_numbers, entry.life, strict=True):
                field.setText(value or "Not stated")
            self.collection_count.setText(self.counts[entry.collection])

    def pick_row(self) -> None:
        if self.puzzle_mode:
            chosen = next_unsolved(self.rows, self.progress, self.current)
        else:
            chosen = self.last_pick = random_opponent(self.rows, self.last_pick)
            self.pick.setText("Re-roll opponent")
        if chosen:
            self.results.setCurrentIndex(self.results.rows.index(self.rows.index(chosen), 0))
            self.results.setFocus()

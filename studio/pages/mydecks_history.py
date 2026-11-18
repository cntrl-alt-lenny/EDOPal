"""History, missing decks and restoring a selected version as a separate copy."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import APP_NAME
from decks import Catalog, Sections, compare, preview
from decks.history import RETENTION_DAYS, VERSIONS_PER_DECK, DeckStore, Timeline, Track, Version
from studio import theme as t
from studio.pages.mydecks_checks import ReadingPane, comparison_html
from studio.pages.mydecks_logic import quantity
from studio.pages.mydecks_widgets import Screen, button
from studio.tasks import Job, TaskRunner, WorkerContext
from studio.widgets import ElidedLabel, Panel, label


def _date(stamp: int, pattern: str) -> str:
    try:
        return datetime.fromtimestamp(stamp / 1e9).strftime(pattern)
    except (OSError, OverflowError, ValueError):
        return "Unknown date"


def _summary(before: Sections, after: Sections, catalog: Catalog) -> str:
    moves: Counter[str] = Counter()
    edits: Counter[tuple[str, int]] = Counter()
    for change in compare(before, after, separate_counts=True):
        if change.kind == "moved":
            moves[change.destination.title()] += change.copies
        else:
            edits[change.kind, change.card_id] += change.copies
    parts = []
    for (kind, code), count in edits.most_common(3):
        name = catalog.cards[code].name if code in catalog.cards else f"Card #{code}"
        parts.append(f"{'+' if kind == 'added' else '−'}{count} {name}")
    text = ", ".join(parts)
    if len(edits) > len(parts):
        text += f", +{len(edits) - len(parts)} more changes"
    return (
        " · ".join(
            ([text] if text else [])
            + [f"{quantity(n, 'card')} moved to {section}" for section, n in moves.items()]
        )
        or "Card order changed"
    )


class MissingDecks(Screen):
    def __init__(
        self,
        tracks: tuple[Track, ...],
        game: Path,
        back: Callable[[], object],
        open_history: Callable[[Track], None],
    ) -> None:
        super().__init__("Missing decks", back)
        panel = self.panel()
        panel.body.addWidget(
            label("Recover the last saved version of a deck that has disappeared.", "body", True)
        )
        self.rows = QListWidget()
        self.rows.setObjectName("installList")
        self.rows.setAccessibleName("Missing decks with saved versions")
        for track in tracks:
            item = QListWidgetItem(track.path.relative_to(game / "deck").as_posix())
            item.setToolTip(str(track.path))
            self.rows.addItem(item)
        panel.body.addWidget(self.rows, 1)
        action = button(
            "Open history", lambda: open_history(tracks[self.rows.currentRow()]), "primary", "arrow"
        )
        panel.body.addWidget(action, 0, Qt.AlignmentFlag.AlignRight)
        self.rows.itemActivated.connect(lambda _: action.click())
        self.rows.setCurrentRow(0)
        self.initial = self.rows


class HistoryScreen(Screen):
    def __init__(
        self,
        track: Track,
        store: DeckStore,
        catalog: Catalog,
        tasks: TaskRunner,
        back: Callable[[], object],
        save: Callable[[str, Sections], None],
    ) -> None:
        super().__init__("History", back)
        self.body.setSpacing(t.SM)
        self.track, self.store, self.catalog, self.tasks = track, store, catalog, tasks
        self.save = save
        self.versions: tuple[Version, ...] = ()
        self.timeline: Timeline | None = None
        self.job: Job | None = None
        self.generation = 0
        name = ElidedLabel()
        name.setProperty("role", "subheading")
        name.set_value(str(track.path), track.path.stem)
        self.body.addWidget(name)
        panel = self.panel()
        self.history_panel = panel
        self.status = label("", "body", True)
        panel.body.addWidget(self.status)
        self.retry = button("Retry", self.load, "primary")
        panel.body.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)
        area = QHBoxLayout()
        self.rows = QListWidget()
        self.rows.setObjectName("installList")
        self.rows.setAccessibleName("Saved deck versions, newest first")
        self.rows.setMinimumHeight(t.TILE_H)
        self.rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows.setTextElideMode(Qt.TextElideMode.ElideRight)
        area.addWidget(self.rows, 2)
        right = QVBoxLayout()
        self.compared_to = label("Changes to the current deck", "subheading")
        right.addWidget(self.compared_to)
        self.report = ReadingPane("Changes since the selected deck version")
        right.addWidget(self.report, 1)
        area.addLayout(right, 3)
        panel.body.addLayout(area, 1)
        self.empty = label("", "body", True)
        panel.body.addWidget(self.empty)
        self.actions = Panel(t.MD)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setAccessibleName("Name for the restored copy")
        self.name.setPlaceholderText("Name for the restored copy")
        row.addWidget(self.name, 1)
        self.restore = button("Restore as a copy", self._restore, "primary", "download")
        row.addWidget(self.restore)
        self.actions.body.addLayout(row)
        self.body.addWidget(
            label(
                f"{APP_NAME} keeps up to {VERSIONS_PER_DECK} previous versions per deck for {RETENTION_DAYS} days.",
                "muted",
                True,
            )
        )
        self.body.addWidget(self.actions)
        self.filler = QWidget()
        self.body.addWidget(self.filler, 1)
        self.rows.currentRowChanged.connect(self._select)
        self.name.textChanged.connect(lambda text: self.restore.setEnabled(bool(text.strip())))
        self.load()

    def leave(self) -> None:
        self.generation += 1
        if self.job is not None:
            self.job.cancel()

    def _set_versions_visible(self, visible: bool) -> None:
        for widget in (self.rows, self.report, self.compared_to, self.actions):
            widget.setVisible(visible)
        self.empty.setVisible(not visible)
        self.filler.setVisible(not visible)
        self.body.setStretchFactor(self.history_panel, int(visible))

    def load(self) -> None:
        self.leave()
        generation = self.generation
        self._set_versions_visible(False)
        self.retry.hide()
        self.status.setText("Reading history…")
        self.empty.clear()
        store, track, catalog = self.store, self.track, self.catalog

        def work(worker: WorkerContext) -> tuple[Timeline, tuple[str, ...]]:
            timeline = store.timeline(track)
            newer = (timeline.latest, *timeline.previous)
            summaries = tuple(
                _summary(version.sections, newer[i].sections, catalog)
                for i, version in enumerate(timeline.previous)
                if not worker.cancel.is_set()
            )
            return timeline, summaries

        self.job = self.tasks.start(work)
        self.job.result.connect(
            lambda result: self._loaded(*result) if generation == self.generation else None
        )
        self.job.error.connect(lambda _: self._failed() if generation == self.generation else None)

    def _failed(self) -> None:
        self.status.setText("Couldn’t read this deck’s history.")
        self.empty.setText("Check that your settings folder is available, then try again.")
        self.retry.show()

    def _loaded(self, timeline: Timeline, summaries: tuple[str, ...]) -> None:
        self.timeline = timeline
        self.versions = (
            (timeline.latest, *timeline.previous) if timeline.missing else timeline.previous
        )
        self.status.setText(
            "This deck is missing from the game folder. You can restore a saved version as a copy."
            if timeline.missing
            else "Select a saved version to compare it with the latest scan."
        )
        self.compared_to.setText(
            "Changes to the last saved version"
            if timeline.missing
            else "Changes to the current deck"
        )
        self.rows.clear()
        if not self.versions:
            self.status.setText("No previous versions yet")
            self.empty.setText(
                "Save changes in EDOPro, then refresh My decks to see the previous version."
            )
            return
        if timeline.missing:
            summaries = ("Last seen in the deck folder", *summaries)
        for version, summary in zip(self.versions, summaries, strict=True):
            text = f"{_date(version.modified_ns, '%d %b %Y, %H:%M')}\n{summary}"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setSizeHint(QSize(0, t.HUGE + t.SM))
            self.rows.addItem(item)
        self._set_versions_visible(True)
        self.rows.setCurrentRow(0)
        if self.isVisible():
            self.rows.setFocus()

    def _select(self, row: int) -> None:
        if row < 0 or self.timeline is None:
            return
        self.leave()
        generation = self.generation
        version, latest, catalog = self.versions[row], self.timeline.latest, self.catalog
        stem = re.sub(r'[<>:"/\\|?*]', " ", self.track.path.stem)
        stem = stem.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(". ")
        self.name.setText(f"{stem} restored {_date(version.modified_ns, '%Y-%m-%d')}")
        self.report.setPlainText("Comparing versions…")

        def work(_: WorkerContext) -> str:
            return comparison_html(
                preview(version.sections, catalog),
                preview(latest.sections, catalog),
                separate_counts=True,
            )

        self.job = self.tasks.start(work)
        self.job.result.connect(
            lambda html: self.report.setHtml(html) if generation == self.generation else None
        )
        self.job.error.connect(
            lambda _: (
                self.report.setPlainText("Couldn’t compare these versions.")
                if generation == self.generation
                else None
            )
        )

    def _restore(self) -> None:
        row = self.rows.currentRow()
        if 0 <= row < len(self.versions) and (name := self.name.text().strip()):
            self.save(name, self.versions[row].sections)

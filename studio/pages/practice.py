"""Installation-scoped Practice navigation. All game and journal I/O runs on workers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from practice.bots import Bot
from practice.library import Library, scan
from practice.progress import Progress, ProgressChanged, read_progress, save_progress
from practice.puzzles import Puzzle
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.decks_contents import DeckContents
from studio.pages.mydecks_logic import saved_sections
from studio.pages.practice_browser import Browser
from studio.pages.practice_detail import Journal, bot_guide
from studio.tasks import Job, WorkerContext
from studio.widgets import Button, ElidedLabel, Hero, Panel, ProgressBlock, label, margins


class PracticePage(Page):
    id, title, icon = "practice", "Practice", "practice"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.body = QVBoxLayout(self)
        margins(self.body, t.LG)
        self.body.setSpacing(t.MD)
        self.hero = Hero(
            "Practice",
            "The practice room",
            "Find a sparring partner or work through a duel puzzle.",
            "spark",
        )
        self.hero.setFixedHeight(t.TILE_LARGE)
        self.body.addWidget(self.hero)
        self.info = label("", "muted", True)
        self.body.addWidget(self.info)
        self.info.hide()
        self.message = Panel(t.XL)
        self.message.body.addStretch()
        self.message_title = label("", "heading", True)
        self.message_copy = label("", "body", True)
        self.action = Button("Choose folder", "primary", "folder")
        self.action.clicked.connect(self._action)
        self.message.body.addWidget(self.message_title)
        self.message.body.addWidget(self.message_copy)
        self.message.body.addWidget(self.action, 0, Qt.AlignmentFlag.AlignLeft)
        self.message.body.addStretch()
        self.body.addWidget(self.message, 1)
        self.busy = ProgressBlock()
        self.busy.body.insertStretch(0, 1)
        self.busy.body.addStretch(1)
        self.busy.cancel.clicked.connect(self.cancel_scan)
        self.body.addWidget(self.busy)
        self.busy.hide()
        self.browser: Browser | None = None
        self.detail: QWidget | None = None
        self.journal: Journal | None = None
        self.puzzle: Puzzle | None = None
        self.job: Job | None = None
        self.saving = False
        self.store = Path(context.cfg.config_path).with_name("practice-progress.sqlite3")
        self.library: Library | None = None
        self.records: dict[str, Progress] = {}
        self.previous: Progress | None = None
        self.game = ""
        self.active = False
        self.state = "empty"
        self.store_issue = ""

    def _fit_hero(self) -> None:
        sparse = self.state in {"empty", "error", "scanning", "cancelled"}
        height = t.landing_hero_height(self.hero.width(), self.height()) if sparse else t.TILE_LARGE
        self.hero.setFixedHeight(height)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        inset = max(t.LG, (self.width() - t.CONTENT_MAX) // 2)
        self.body.setContentsMargins(inset, t.LG, inset, t.LG)
        self._fit_hero()

    def _action(self) -> None:
        if not self.game:
            self.ctx.installations_requested.emit()
        else:
            self.start_scan()

    def _display(self, state: str, title: str = "", text: str = "") -> None:
        self.state = state
        self._fit_hero()
        self.hero.setVisible(state != "detail")
        self.message.setVisible(state in {"empty", "error", "cancelled"})
        self.busy.setVisible(state == "scanning")
        self.message_title.setText(title)
        self.message_copy.setText(text)
        self.action.setText("Choose folder" if not self.game else "Scan again")
        if self.browser:
            self.browser.setVisible(state == "browse")
        if self.detail:
            self.detail.setVisible(state == "detail")
        notices = []
        if self.game and not self.ctx.installation.ready:
            notices.append(
                "Card data is not ready. You can browse puzzles while EDOPro finishes its first update."
            )
        if self.store_issue:
            notices.append(self.store_issue)
        self.info.setText(" ".join(notices))
        self.info.setVisible(bool(notices))

    def installation_changed(self, installation: Installation) -> None:
        self._stop_scan()
        self._remove_detail()
        self._remove_browser()
        self.library = None
        self.records = {}
        self.store_issue = ""
        self.game = installation.path
        if self.active:
            self.shown()

    def _stop_scan(self) -> None:
        if self.job:
            self.job.abandon()
            self.job = None

    def cancel_scan(self) -> None:
        self._stop_scan()
        self._display(
            "cancelled",
            "Scan cancelled",
            "Scan again when you’re ready to browse this installation.",
        )
        self.action.setFocus()

    def shown(self) -> None:
        self.active = True
        if not self.game:
            self._display(
                "empty",
                "Choose a game folder",
                "Select your EDOPro installation to find its practice opponents and puzzle collections.",
            )
            self.action.setFocus()
        elif self.browser:
            self._display("browse")
            self.browser.search.setFocus()
        elif not self.job:
            self.start_scan()

    def start_scan(self) -> None:
        self._stop_scan()
        self._remove_browser()
        self.library = None
        self.store_issue = ""
        self.busy.begin("Reading the practice library")
        self._display("scanning")
        self.busy.cancel.setFocus()
        game, store = Path(self.game), self.store

        def work(worker: WorkerContext) -> tuple[Library, dict[str, Progress], str]:
            lib = scan(game, worker.cancel, worker.progress)
            try:
                if store.resolve().is_relative_to(game.resolve()):
                    raise ValueError(
                        "Choose an app config outside the game folder to save progress."
                    )
                return lib, read_progress(store, lib.puzzles), ""
            except ValueError as exc:
                return lib, {}, f"Puzzle progress is unavailable. {exc}"
            except (sqlite3.Error, OSError):
                return (
                    lib,
                    {},
                    "Puzzle progress is unavailable. The saved journal could not be read. You can still browse the library.",
                )

        job = self.job = self.ctx.tasks.start(work, owner=self)
        job.progress.connect(self.busy.detail.setText)
        job.result.connect(self._loaded)
        job.error.connect(
            lambda exc: self._display("error", "Could not read this installation", str(exc))
        )
        job.finished.connect(lambda: setattr(self, "job", None))

    def _loaded(self, result: tuple[Library, dict[str, Progress], str]) -> None:
        self.library, self.records, self.store_issue = result
        browser = self.browser = Browser(
            Path(self.game),
            self.library,
            self.records,
            self.ctx.tasks,
            progress_available=not self.store_issue,
        )
        browser.open_requested.connect(self.open_detail)
        browser.refresh_requested.connect(self.start_scan)
        self.body.addWidget(browser, 1)
        self._display("browse")
        browser.search.setFocus()

    def open_detail(self) -> None:
        browser = self.browser
        if not browser or not (entry := browser.current):
            return
        self._remove_detail()
        self.detail = QWidget()
        layout = QVBoxLayout(self.detail)
        margins(layout, 0)
        row = QHBoxLayout()
        back = Button("Back", "quiet", "back")
        back.clicked.connect(self.escape)
        row.addWidget(back)
        row.addWidget(
            label("MATCHUP GUIDE" if isinstance(entry, Bot) else "PUZZLE LIBRARY", "goldKicker")
        )
        row.addStretch()
        layout.addLayout(row)
        heading = ElidedLabel()
        heading.setProperty("role", "display")
        heading.set_value(entry.name if isinstance(entry, Bot) else entry.title)
        layout.addWidget(heading)
        if isinstance(entry, Bot):
            index = browser.list_choice.currentData()
            guide = QHBoxLayout()
            guide.setSpacing(t.LG)
            guide.addWidget(
                bot_guide(
                    entry,
                    index,
                    browser.list_choice.currentText() if index >= 0 else "",
                    browser.library.opponents.runtime,
                ),
                1,
            )
            if entry.preview:
                cards = DeckContents(self.ctx.tasks)
                guide.addWidget(cards, 1)
                if entry.file in browser.sections:
                    cards.set_sections(browser.sections[entry.file])
                else:
                    game = Path(self.game)
                    job = self.ctx.tasks.start(
                        lambda w: saved_sections(game, entry.preview, w.cancel), owner=cards
                    )
                    job.result.connect(cards.set_sections)
            layout.addLayout(guide, 1)
        else:
            self.puzzle = entry
            self.journal = Journal(
                entry, self.records.get(entry.path, Progress()), available=not self.store_issue
            )
            self.journal.save_requested.connect(self._save)
            self.journal.undo.clicked.connect(self._undo)
            layout.addWidget(self.journal, 1)
        self.body.addWidget(self.detail, 1)
        self._display("detail")
        back.setFocus()

    def _undo(self) -> None:
        if self.previous is not None:
            self._save(self.previous, undo=True)

    def _save(self, after: Progress, *, undo: bool = False) -> None:
        journal, puzzle = self.journal, self.puzzle
        if not journal or not puzzle or self.saving:
            return
        before = self.records.get(puzzle.path, Progress())
        store = self.store
        self.saving = True
        journal.setEnabled(False)
        journal.message.setText("Saving progress…")
        job = self.ctx.tasks.start(
            lambda _: save_progress(store, puzzle, before, after), owner=self
        )

        def saved(_: object) -> None:
            self.records[puzzle.path] = after
            if self.journal is journal:
                self.previous = None if undo else before
                journal.set_value(after)
                journal.undo.setVisible(not undo and before != after)
                journal.message.setText(
                    "Previous status, date and note restored." if undo else "Progress saved."
                )
            if self.browser:
                self.browser.update_counts()
                self.browser.apply()

        def failed(error: Exception) -> None:
            if self.journal is journal:
                if isinstance(error, ProgressChanged):
                    journal.message.setText(
                        "Another window changed this entry. Copy any note you want to keep, go Back and Rescan before saving."
                    )
                elif isinstance(error, ValueError):
                    journal.message.setText(
                        "Enter a date as YYYY-MM-DD, choose Today, or leave it blank."
                    )
                else:
                    journal.message.setText(f"Could not save progress. {error}")

        def finished() -> None:
            self.saving = False
            if self.journal is journal:
                journal.setEnabled(True)

        job.result.connect(saved)
        job.error.connect(failed)
        job.finished.connect(finished)

    def _remove_browser(self) -> None:
        if self.browser:
            self.browser.hide()
            self.body.removeWidget(self.browser)
            self.browser.deleteLater()
            self.browser = None

    def _remove_detail(self) -> None:
        if self.detail:
            self.detail.hide()
            self.body.removeWidget(self.detail)
            self.detail.deleteLater()
        self.detail = self.journal = self.puzzle = self.previous = None

    def _can_leave(self) -> bool:
        if self.saving:
            return False
        if (
            self.journal
            and self.puzzle
            and not self.store_issue
            and self.journal.value() != self.records.get(self.puzzle.path, Progress())
        ):
            return self.ctx.confirm(
                "Discard journal edits?", "Leave this puzzle and discard your unsaved changes?"
            )
        return True

    def escape(self) -> bool:
        if self.detail:
            if self._can_leave():
                self._remove_detail()
                self._display("browse")
                self.browser.results.setFocus()
            return True
        if self.job:
            self.cancel_scan()
            return True
        return False

    def hidden(self) -> bool:
        if not self._can_leave():
            return False
        self.active = False
        self._stop_scan()
        self._remove_detail()
        return True

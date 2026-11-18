"""Browse bundled card pools and manage the selected game's format lists."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPoint, QPropertyAnimation, QSignalBlocker, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from config import APP_NAME
from formats import installation as engine
from formats.catalogue import Catalogue, copy_settings
from formats.installation import GameBlocked, State
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.formats_views import (
    DATA,
    CatalogueList,
    Sheet,
    host_sheet,
    manage_sheet,
    pool_sheet,
    sample_sheet,
)
from studio.tasks import Job, WorkerContext
from studio.widgets import (
    Button,
    GameClosedBlock,
    Hero,
    Panel,
    ProgressBlock,
    SearchField,
    label,
    margins,
)


class FormatsPage(Page):
    id, title, icon = "formats", "Formats", "formats"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.catalogue: Catalogue | None = None
        self.states: dict[int, State] = {}
        self.region = "TCG"
        self.selected: int | None = None
        self.mode = "pool"
        self.generation = 0
        self.writing: Job | None = None
        self.loaded = False
        self.receipt = ""
        self.last_change: dict[int, engine.PreviousFile] = {}

        frame = QWidget()
        frame.setMaximumWidth(t.CONTENT_MAX)
        outer = QHBoxLayout(self)
        margins(outer, 0)
        outer.addWidget(frame)
        body = QVBoxLayout(frame)
        margins(body, t.LG)
        body.setSpacing(t.LG)
        self.hero = Hero(
            "Format Library",
            "Formats",
            "Explore historical card pools and find the settings for your table.",
            "spark",
        )
        self.hero.setFixedHeight(t.TILE_LARGE)
        body.addWidget(self.hero)
        columns = QHBoxLayout()
        columns.setSpacing(t.LG)
        body.addLayout(columns, 1)
        self.sidebar = Panel(t.MD)
        columns.addWidget(self.sidebar, 1)
        self.sidebar.body.addWidget(label("Browse formats", "subheading"))
        regions = QHBoxLayout()
        self.region_buttons = {}
        for region in ("TCG", "OCG"):
            button = Button(region)
            button.setAccessibleName(f"Show {region} formats")
            button.setCheckable(True)
            button.setStyleSheet(
                f"QPushButton:checked {{ color: {t.GOLD}; background: {t.ACTIVE}; border-color: {t.GOLD}; }}"
            )
            button.clicked.connect(lambda _, r=region: self.set_region(r))
            regions.addWidget(button)
            self.region_buttons[region] = button
        self.sidebar.body.addLayout(regions)
        self.search = SearchField("Search formats")
        self.sidebar.body.addWidget(self.search)
        self.list = CatalogueList()
        self.sidebar.body.addWidget(self.list, 1)
        self.tally = label("", "muted", True)
        self.sidebar.body.addWidget(self.tally)
        self.manage = Button("Manage lists", "secondary", "library")
        self.sidebar.body.addWidget(self.manage)
        self.manage.clicked.connect(lambda: self.show_mode("manage"))
        self.search.textChanged.connect(self.filter)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemActivated.connect(lambda _: self.show_mode("pool", focus=True))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        columns.addWidget(self.scroll, 2)
        self.slide = QPropertyAnimation(self)
        self.slide.setPropertyName(b"pos")
        self.slide.setDuration(t.MOTION_MS)
        self.slide.setEndValue(QPoint())

    def shown(self) -> None:
        self.search.setFocus()
        self.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        threshold = t.TALL_HEIGHT + t.HERO_MIN
        if (
            self.loaded
            and self.mode == "pool"
            and (event.size().height() >= threshold) != (event.oldSize().height() >= threshold)
        ):
            self.show_mode("pool")

    def installation_changed(self, installation: Installation) -> None:
        self.generation += 1
        self.states.clear()
        self.receipt = ""
        self.last_change.clear()
        self.loaded = False
        self.mode = "pool"
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        self.generation += 1
        generation = self.generation
        game = self.ctx.installation.path
        ready = self.ctx.installation.ready
        existing = self.catalogue
        self.loaded = False
        self.manage.setEnabled(False)
        self.sidebar.setEnabled(False)
        self._message("Reading formats", "Checking the lists for this installation…")

        def work(_: WorkerContext) -> tuple[Catalogue, dict]:
            catalogue = existing or Catalogue.load()
            states = engine.statuses(Path(game), catalogue.rows) if ready else {}
            return catalogue, states

        def done(result: tuple[Catalogue, dict]) -> None:
            if generation != self.generation:
                return
            self.catalogue, self.states = result
            self.loaded = True
            self.sidebar.setEnabled(True)
            self.manage.setEnabled(ready)
            if self.selected is None:
                self.selected = next(
                    (r["id"] for r in self.catalogue.rows if r["name"] == "Edison"), None
                )
            self.filter()
            if self.isVisible() and not self.search.hasFocus():
                self.search.setFocus()

        job = self.ctx.tasks.start(work, owner=self)
        job.result.connect(done)
        job.error.connect(
            lambda exc: self._error(exc, self.refresh) if generation == self.generation else None
        )

    def set_region(self, region: str) -> None:
        self.region = region
        self.mode = "pool"
        self.receipt = ""
        self.filter()

    def filter(self) -> None:
        if not self.loaded or not self.catalogue:
            return
        for key, button in self.region_buttons.items():
            button.setChecked(key == self.region)
        rows = self.catalogue.select(self.region, query=self.search.text())
        with QSignalBlocker(self.list):
            self.list.fill(rows, self.states, self.selected)
        installed = sum(state in (State.INSTALLED, State.OLD) for state in self.states.values())
        self.tally.setText(
            f"{len(rows)} formats   ·   {installed} installed"
            if self.ctx.installation.ready
            else f"{len(rows)} formats"
        )
        if self.mode not in ("pool", "host", "manage", "evidence"):
            self.mode = "pool"
        self._selected()

    def _selected(self) -> None:
        item = self.list.currentItem()
        if not item or not (row := item.data(DATA)):
            empty = self._message(
                "No matching formats",
                f"Try another name, year or banlist in the {self.region} catalogue.",
            )
            empty.action("clear", "Clear search", self.search.clear, True, "search")
            return
        if self.selected != row["id"]:
            self.receipt = ""
            self.mode = "pool"
        self.selected = row["id"]
        self.show_mode(self.mode)

    @property
    def row(self) -> dict:
        return self.list.currentItem().data(DATA)

    def _put(self, view: Sheet, *, animate: bool = False) -> None:
        self.slide.stop()
        if old := self.scroll.takeWidget():
            old.deleteLater()
        self.scroll.setWidget(view)
        self.scroll.verticalScrollBar().setValue(0)
        if animate and self.isVisible():
            self.slide.setTargetObject(view)
            self.slide.setStartValue(QPoint(t.XL, 0))
            self.slide.start()

    def _message(self, title: str, detail: str) -> Sheet:
        sheet = Sheet()
        sheet.line(title, "heading")
        sheet.line(detail)
        self._put(sheet)
        return sheet

    def show_mode(self, mode: str, *, focus: bool = False) -> None:
        if not self.catalogue or self.writing:
            return
        prior = self.mode
        self.mode = mode
        if mode == "manage":
            view = manage_sheet(
                self.catalogue,
                self.states,
                self.region,
                self.row["era"] if self.list.currentItem() else "",
                self.escape,
                self.ctx.confirm,
                self.start,
            )
        elif not self.list.currentItem():
            self._message("No matching formats", "Try another name, year or banlist.")
            return
        else:
            row = self.row
            recipe = self.catalogue.recipes[row["id"]]

            def copy() -> None:
                QApplication.clipboard().setText(copy_settings(row, recipe))
                self.ctx.notify("Host settings copied.")

            if mode == "evidence":
                view = sample_sheet(row, self.escape)
            elif mode == "host":
                view = host_sheet(row, recipe, self.escape, copy)
            else:
                view = pool_sheet(
                    row,
                    recipe,
                    self.states.get(row["id"], State.ABSENT),
                    self.ctx.installation,
                    lambda remove: self.start([row], remove),
                    self.ctx.installations_requested.emit,
                    lambda: self.show_mode("host", focus=True),
                    lambda: self.show_mode("evidence", focus=True),
                    inline_copy=copy if self.height() >= t.TALL_HEIGHT + t.HERO_MIN else None,
                )
        if self.receipt and mode != "host":
            view.body.insertWidget(0, label(self.receipt, "notice", True))
        if self.last_change and (mode == "manage" or (self.receipt and mode == "pool")):
            self._undo_control(view)
        self._put(view, animate=mode != prior)
        if (focus or mode != prior) and (button := view.findChild(Button)):
            button.setFocus()

    def _undo_control(self, panel: Sheet) -> None:
        restore = sum(x.data is not None for x in self.last_change.values())
        remove = len(self.last_change) - restore
        parts = []
        if remove:
            parts.append(f"remove {remove} added list{'s' if remove != 1 else ''}")
        if restore:
            parts.append(f"put back {restore} earlier list{'s' if restore != 1 else ''}")
        rows = [r for r in self.catalogue.rows if r["id"] in self.last_change]
        if len(rows) == 1:
            parts = [
                f"restore the previous {rows[0]['name']} list"
                if restore
                else f"remove the added {rows[0]['name']} list"
            ]
        bar = QHBoxLayout()
        undo = Button("Undo last change", "secondary", "back")
        undo.setObjectName("undo")
        undo.setToolTip(", ".join(r["name"] for r in rows))
        undo.clicked.connect(lambda: self.start(rows, undo=True))
        bar.addWidget(undo)
        bar.addWidget(
            label(
                (" and ".join(parts)).capitalize()
                + f". Available until your next change, a folder switch or closing {APP_NAME}.",
                "muted",
                True,
            ),
            1,
        )
        panel.body.insertLayout(1, bar)

    def start(self, rows: list[dict], remove: bool = False, *, undo: bool = False) -> None:
        if (
            self.writing
            or not self.loaded
            or not self.ctx.installation.ready
            or not self.catalogue
            or not rows
        ):
            return
        game = Path(self.ctx.installation.path)
        catalogue = self.catalogue
        recorded = engine.Changes()
        earlier = dict(self.last_change) if undo else None
        previous = self.mode if self.mode in ("manage", "pool") else "pool"
        self.receipt = ""
        panel = Sheet()
        panel.line("DECK EDITOR LISTS", "goldKicker")
        panel.line(rows[0]["name"] if len(rows) == 1 else "Manage lists", "display")
        self.progress = ProgressBlock()
        self.progress.begin(
            "Undoing list changes" if undo else "Removing lists" if remove else "Adding lists"
        )
        self.progress.advance_to(0, len(rows))
        self.progress.detail.setText("Keep this installation selected until the changes finish.")
        panel.body.addWidget(self.progress)
        panel.line("IN THIS CHANGE", "goldKicker")
        pending = label("", "body", True)
        panel.body.addWidget(pending)

        def remaining(done: int) -> None:
            following = rows[done : done + 8]
            pending.setText(
                "\n".join(r["name"] for r in following)
                + (
                    f"\n… and {len(rows) - done - len(following)} more"
                    if done + len(following) < len(rows)
                    else ""
                )
            )

        remaining(0)
        self._put(panel)
        self.sidebar.setEnabled(False)
        self.ctx.set_busy(True)

        def work(worker: WorkerContext) -> tuple[engine.Changes | Exception, dict[int, State]]:
            try:
                result = engine.change(
                    game,
                    catalogue,
                    rows,
                    remove=remove,
                    cancel=worker.cancel,
                    progress=worker.progress,
                    undo=earlier,
                    receipt=recorded,
                )
            except (OSError, ValueError, GameBlocked) as exc:
                # A bulk change can fail after publishing several complete lists.
                return exc, engine.statuses(game, catalogue.rows)
            return result, engine.statuses(game, catalogue.rows)

        job = self.writing = self.ctx.tasks.start(work, owner=self)
        self.progress.cancel.clicked.connect(job.cancel)
        self.progress.cancel.setFocus()

        def progress(item: tuple[int, int, str]) -> None:
            self.progress.advance_to(item[0], item[1])
            self.progress.detail.setText(
                f"{item[2]} · Keep this installation selected until the changes finish."
            )
            remaining(item[0])

        job.progress.connect(progress)

        def finished(result: engine.Changes | Exception) -> None:
            self.writing = None
            self.ctx.set_busy(False)
            self.sidebar.setEnabled(True)
            self.mode = previous
            if undo:
                for fid in recorded.previous:
                    self.last_change.pop(fid, None)
            elif recorded.previous:
                self.last_change = recorded.previous
            if isinstance(result, Exception):

                def retry() -> None:
                    self.mode = previous
                    self.start(
                        [r for r in rows if not undo or r["id"] in self.last_change],
                        remove,
                        undo=undo,
                    )

                self._error(result, retry, rows)
                return
            count = len(result.changed)
            verb = (
                "returned to its previous state"
                if undo and count == 1
                else "returned to their previous states"
                if undo
                else "removed"
                if remove
                else "added or updated"
            )
            self.receipt = (
                "Stopped. " if result.stopped else ""
            ) + f"{count} list{'s' if count != 1 else ''} {verb}."
            if result.kept:
                self.receipt += " Kept changed files: " + ", ".join(result.kept) + "."
            self.filter()

        def received(packet: tuple[engine.Changes | Exception, dict[int, State]]) -> None:
            result, self.states = packet
            if isinstance(result, Exception):
                self.filter()
            finished(result)

        job.result.connect(received)
        job.error.connect(finished)

    def _error(self, error: Exception, retry: Callable, rows: list[dict] | None = None) -> None:
        self.mode = "error"
        panel = Sheet()
        if self.loaded:
            panel.action("back", "Back to format", self.escape, icon="back")
        if rows:
            panel.line("DECK EDITOR LISTS", "goldKicker")
            panel.line(
                rows[0]["name"] if len(rows) == 1 else f"{len(rows)} selected lists", "display"
            )
        if isinstance(error, GameBlocked):
            block = GameClosedBlock()
            block.show_reason(str(error))
            block.retry.setObjectName("retry")
            block.retry.clicked.connect(retry)
            panel.body.addWidget(block)
        else:
            panel.line("Couldn’t finish the list operation", "heading")
            panel.line(str(error))
            panel.line(
                "Check the game folder and available space, then retry. Completed changes are kept."
            )
            panel.action("retry", "Retry", retry, True)
        if rows:
            if len(rows) == 1:
                row = rows[0]
                panel.line(f"{row['category']}   ·   {row['date']}   ·   {row['banlist']} banlist")
                panel.metrics(row)
                panel.line(row["validation"]["label"], "subheading")
                panel.line(f"Deck editor list   ·   {row['display']}")
            else:
                panel.line("IN THIS CHANGE", "goldKicker")
                panel.line(", ".join(r["name"] for r in rows))
        self._put(panel)
        panel.findChild(Button, "retry").setFocus()
        if self.last_change:
            self._undo_control(panel)

    def hidden(self) -> bool:
        return self.writing is None

    def escape(self) -> bool:
        if self.writing:
            return True
        if self.mode in ("host", "manage", "error", "evidence") and self.loaded:
            self.mode = "pool"
            self.refresh()
            self.list.setFocus()
            return True
        if self.search.text():
            self.search.clear()
            return True
        return False

    def request_close(self) -> bool:
        if self.writing:
            self.writing.cancel()
        return True

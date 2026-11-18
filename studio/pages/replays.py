from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QComboBox, QFileDialog, QVBoxLayout

from music.settings import _game_running
from replays import (
    AnnotationStore,
    ReplayEntry,
    ReplayNotes,
    export_replay,
    index_replays,
    preserve_last_replay,
    search_replays,
)
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.health_layout import ToolPage, row
from studio.pages.replays_details import ReplayForm
from studio.pages.replays_library import (
    SORTS,
    details_line,
    folder_stamp,
    format_tags,
    last_to_save,
    played,
    reveal_file,
)
from studio.pages.replays_list import ReplayList
from studio.tasks import Work, WorkerContext
from studio.widgets import Button, Panel, SearchField, SectionHeader, glyph, label


class GameOpen(RuntimeError):
    """The chosen installation must be closed for this write."""


class ChangedRecording(ValueError):
    """The library needs a new snapshot before this recording can be saved."""


def require_closed(game: Path) -> None:
    if _game_running(game):
        raise GameOpen("After the game closes, choose Retry to save the file.")


class ReplaysPage(ToolPage):
    id, title, icon = "replays", "Replays", "replays"
    mood = "replays"
    introduction = "Keep your last duel and find the ones worth watching again."

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.entries: tuple[ReplayEntry, ...] = ()
        self.selected: ReplayEntry | None = None
        self.last: ReplayEntry | None = None
        self.writing = False
        self.query, self.order = "", "Newest first"
        self.undo_notes: dict[str, tuple[ReplayNotes, ReplayNotes]] = {}
        self.saved: Path | None = None
        self.watch = QTimer(self)
        self.watch.setInterval(t.NOTICE_MS)
        self.watch.timeout.connect(self._watch_folder)
        self.stamp: tuple[int, ...] = ()

    def _watch_folder(self) -> None:
        if self.state not in {"library", "empty"} or self.read_job:
            return
        game = Path(self.ctx.installation.path)

        def changed(stamp: tuple[int, ...]) -> None:
            if stamp != self.stamp:
                self.refresh()

        self.read(lambda _: folder_stamp(game), changed, lambda _: self.refresh())

    def installation_changed(self, installation: Installation) -> None:
        self.query, self.order = "", "Newest first"
        self.saved = None
        if not self.writing:
            super().installation_changed(installation)

    def refresh(self) -> None:
        self.stop_read()
        game = self.ctx.installation.path
        self.selected, self.entries = None, ()
        if not game:
            self.no_game()
            return
        profile = Path(self.ctx.cfg.config_path).parent
        self.state_view(
            "loading",
            "Reading your replays…",
            "Looking for saved duels and your most recent recording.",
        )

        def scan(_: WorkerContext) -> tuple[tuple[int, ...], tuple[ReplayEntry, ...]]:
            if not Path(game).is_dir():
                raise FileNotFoundError(
                    "The installation folder is unavailable. Reconnect the drive or choose another folder."
                )
            return folder_stamp(Path(game)), index_replays(
                game, annotations=AnnotationStore(profile)
            )

        self.read(
            scan,
            self._indexed,
            lambda exc: self.state_view(
                "error", "The library could not open", str(exc), "Retry", self.refresh
            ),
        )

    def _indexed(self, result: tuple[tuple[int, ...], tuple[ReplayEntry, ...]]) -> None:
        self.stamp, self.entries = result
        self.watch.start()
        self.library()

    def library(self) -> None:
        self.selected = None
        self.surface("library")
        self.last = last_to_save(self.entries)
        self.banner("Replay library", self.introduction)
        if not self.ctx.installation.ready:
            self.body.addWidget(
                label(
                    "EDOPro is waiting for card data. You can still manage replays here.",
                    "body",
                    True,
                )
            )
        if self.saved and any(e.path == self.saved for e in self.entries):
            self._saved_copy()
        if self.last:
            panel = Panel(t.LG)
            line = row()
            line.addWidget(glyph("replays", t.ICON_BIG))
            copy = QVBoxLayout()
            copy.setSpacing(t.XS)
            copy.addWidget(label("Last duel  ·  " + played(self.last), "subheading", True))
            copy.addWidget(
                label("EDOPro replaces this recording after your next duel.", "body", True)
            )
            line.addLayout(copy, 1)
            self.preserve = Button("Save my last duel", "primary", "download")
            self.preserve.clicked.connect(self._preserve)
            line.addWidget(self.preserve)
            panel.body.addLayout(line)
            self.body.addWidget(panel)
        heading = row()
        heading.addWidget(label("Your duels", "heading"), 1)
        self.count = label("", "muted")
        heading.addWidget(self.count)
        refresh = Button("Refresh", "quiet")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh)
        self.body.addLayout(heading)
        if not self.entries:
            self.state = "empty"
            panel = Panel()
            panel.body.addStretch()
            panel.body.addWidget(glyph("replays", t.BRAND_ICON))
            panel.body.addWidget(
                SectionHeader(
                    "Your first replay goes here",
                    "Finish a duel in EDOPro and save the replay. Return here and choose Refresh to see it.",
                )
            )
            panel.body.addStretch()
            self.body.addWidget(panel, 1)
            refresh.setFocus()
            return
        controls = row()
        self.search = SearchField("Search players, labels, notes or tags")
        self.search.setText(self.query)
        controls.addWidget(self.search, 1)
        controls.addWidget(label("Sort by", "muted"))
        self.sort = QComboBox()
        self.sort.setAccessibleName("Sort replays")
        self.sort.addItems(list(SORTS))
        self.sort.setCurrentText(self.order)
        self.sort.setStyleSheet(f"""QComboBox {{background:{t.RAISED}; border:{t.HAIR}px solid {t.EDGE};
            border-radius:{t.R_SMALL}px; padding:{t.SM}px {t.MD}px; min-width:{t.TILE_H}px;}}
            QComboBox:focus {{border-color:{t.PALE};}}""")
        controls.addWidget(self.sort)
        self.body.addLayout(controls)
        self.list = ReplayList()
        self.list.setMinimumHeight(t.TILE_H * 2)
        self.body.addWidget(self.list, 1)
        self.no_matches = Panel()
        self.no_matches.body.addStretch()
        self.no_matches.body.addWidget(
            SectionHeader("No matching duels", "Try another player, label or tag.")
        )
        clear = Button("Clear search")
        clear.clicked.connect(self.search.clear)
        self.no_matches.body.addWidget(clear, 0, Qt.AlignmentFlag.AlignLeft)
        self.no_matches.body.addStretch()
        self.body.addWidget(self.no_matches, 1)
        self.list.clicked.connect(self._select)
        self.list.activated.connect(self._select)
        self.search.textChanged.connect(self._filter)
        self.sort.currentTextChanged.connect(self._filter)
        self._filter()
        (self.preserve if self.last else self.search).setFocus()

    def _filter(self) -> None:
        self.query, self.order = self.search.text(), self.sort.currentText()
        entries = search_replays(self.entries, self.query, sort=SORTS[self.order])
        self.list.records.replace(entries)
        self.list.setVisible(bool(entries))
        self.no_matches.setVisible(not entries)
        self.count.setText(
            f"{len(entries):,} of {len(self.entries):,}"
            if self.query
            else f"{len(entries):,} recording{'s' if len(entries) != 1 else ''}"
        )
        if entries:
            self.list.setCurrentIndex(self.list.records.index(0))

    def _select(self, index: QModelIndex) -> None:
        if self.state == "library" and index.isValid():
            self.detail(self.list.records.entries[index.row()])

    def _saved_copy(self) -> None:
        panel = Panel(t.MD)
        line = row()
        line.addWidget(glyph("check", colour=t.GREEN))
        line.addWidget(
            label(
                "Your saved copy appears in Replays next time you start EDOPro. To undo, show the copy and move it to the trash.",
                "body",
                True,
            ),
            1,
        )
        button = Button("Show saved copy", "secondary", "folder")
        path = self.saved
        button.clicked.connect(lambda: self._reveal(path))
        line.addWidget(button)
        panel.body.addLayout(line)
        self.body.addWidget(panel)

    def _preserve(self) -> None:
        entry = self.last
        if not entry:
            return
        game = Path(self.ctx.installation.path)

        def save(_: WorkerContext) -> Path:
            require_closed(game)
            if entry.replay_id is None:
                raise ChangedRecording("Refresh the library once the recording can be read.")
            path = preserve_last_replay(game, source=entry.path, expected_id=entry.replay_id)
            if not path:
                raise ChangedRecording(
                    "The recording changed or could not be read. Refresh the library after the duel has finished."
                )
            return path

        def success(path: Path) -> None:
            self.saved = path
            self.refresh()

        self._write("Saving your last duel…", save, success, self._preserve, self.refresh)

    def detail(self, entry: ReplayEntry) -> None:
        self.stop_read()
        self.selected = entry
        self.surface("detail")
        back = self.back_button(self._back)
        self.banner("Replay details", played(entry) + "  ·  " + details_line(entry))
        prior = self.undo_notes.get(entry.replay_id or "")
        self.form = ReplayForm(entry, bool(prior and prior[0] == entry.annotation))
        self.body.addWidget(self.form, 1)
        self.form.arrange(self.width(), self.height())
        self.form.reveal.clicked.connect(lambda: self._reveal(entry.path))
        self.form.export.clicked.connect(self._export)
        self.form.save.clicked.connect(lambda: self._store(self.form.draft()))
        self.form.undo.clicked.connect(self._undo)
        back.setFocus()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.state == "detail" and hasattr(self, "form"):
            self.form.arrange(self.width(), self.height())

    def _undo(self) -> None:
        if self.selected and self.selected.replay_id:
            self._store(self.undo_notes[self.selected.replay_id][1], undo=True)

    def _store(self, annotation: ReplayNotes, *, undo: bool = False) -> None:
        entry = self.selected
        if not entry or not entry.replay_id:
            return
        key = entry.replay_id
        profile = Path(self.ctx.cfg.config_path).parent
        game = Path(self.ctx.installation.path)

        def save(_: WorkerContext) -> None:
            if profile.resolve().is_relative_to(game.resolve()):
                require_closed(game)
            AnnotationStore(profile).set(key, annotation)

        def success(_: object) -> None:
            if undo:
                self.undo_notes.pop(key, None)
            else:
                self.undo_notes[key] = (annotation, entry.annotation)
            self.entries = tuple(
                replace(e, annotation=annotation) if e.replay_id == key else e for e in self.entries
            )
            self.detail(replace(entry, annotation=annotation))
            self.ctx.notify(
                "Previous label, tags and notes restored."
                if undo
                else "Label, tags and notes saved."
            )
            if not undo:
                self.form.undo.setFocus()
                self.scroll.ensureWidgetVisible(self.form.undo)

        self._write(
            "Saving your notebook…",
            save,
            success,
            lambda: self._store(annotation, undo=undo),
            lambda: self._restore_draft(entry, annotation),
        )

    def _restore_draft(self, entry: ReplayEntry, draft: ReplayNotes) -> None:
        self.detail(entry)
        self.form.name.setText(draft.label)
        self.form.tags.setText(format_tags(draft.tags))
        self.form.notes.setPlainText(draft.notes)

    def _export(self) -> None:
        entry = self.selected
        if not entry:
            return
        profile = Path(self.ctx.cfg.config_path).parent
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export replay with notes",
            str(profile / "Duel with notes.zip"),
            "Replay bundle (*.zip)",
        )
        if not destination:
            return
        game = Path(self.ctx.installation.path)
        path = Path(destination)

        def export(_: WorkerContext) -> Path:
            if path.resolve().is_relative_to(game.resolve()):
                require_closed(game)
            try:
                return export_replay(entry, path)
            except FileExistsError as exc:
                raise ValueError(
                    "A file already has that name. Choose another name for your export."
                ) from exc

        def success(_: Path) -> None:
            self.detail(entry)
            self.ctx.notify("Replay and notes exported.")

        self._write(
            "Exporting your replay…", export, success, self._export, lambda: self.detail(entry)
        )

    def _reveal(self, path: Path) -> None:
        job = self.ctx.tasks.start(lambda _: reveal_file(path))
        job.result.connect(lambda shown: None if shown else self.open_folder(path.parent))
        job.error.connect(lambda exc: self.ctx.notify(str(exc)))

    def _write(
        self, message: str, work: Work, done: Callable, retry: Callable, back: Callable
    ) -> None:
        if self.writing:
            return
        game = self.ctx.installation.path
        self.stop_read()
        self.writing = True
        self.ctx.set_busy(True)
        self.state_view(
            "busy",
            message,
            "Stay on this page until saving finishes. Your installation is locked for this step.",
        )
        job = self.ctx.tasks.start(work)

        def release() -> bool:
            self.writing = False
            self.ctx.set_busy(False)
            if self.ctx.installation.path != game:
                self.refresh()
                return False
            return True

        def failed(exc: Exception) -> None:
            if release():
                stale = isinstance(exc, ChangedRecording)
                headline = (
                    "Close EDOPro to save"
                    if isinstance(exc, GameOpen)
                    else "The file could not be saved"
                )
                self.state_view(
                    "write_error",
                    headline,
                    str(exc),
                    "Refresh" if stale else "Retry",
                    self.refresh if stale else retry,
                )
                self.retry_back = back
                self.back_button(back)

        job.result.connect(lambda value: done(value) if release() else None)
        job.error.connect(failed)

    def _may_leave(self) -> bool:
        if (
            self.state == "detail"
            and self.selected
            and self.form.draft() != self.selected.annotation
        ):
            return self.ctx.confirm(
                "Discard edits?", "Leave without saving your changes to the label, tags and notes?"
            )
        return True

    def _back(self) -> None:
        if self._may_leave():
            previous = self.selected
            self.library()
            if previous and previous in self.list.records.entries:
                self.list.setCurrentIndex(
                    self.list.records.index(self.list.records.entries.index(previous))
                )
                self.list.setFocus()

    def escape(self) -> bool:
        if self.state == "detail":
            self._back()
        elif self.state == "write_error":
            self.retry_back()
        elif not self.writing:
            return False
        return True

    def hidden(self) -> bool:
        if self.writing or not self._may_leave():
            return False
        self.watch.stop()
        return super().hidden()

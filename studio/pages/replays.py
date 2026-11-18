from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QModelIndex, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QFileDialog

import desktop_helpers
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
from studio.pages.health_layout import ToolPage, confirm_action, task_heading
from studio.pages.replays_details import ReplayForm
from studio.pages.replays_library import (
    SORTS,
    SavedCopy,
    details_line,
    folder_stamp,
    format_tags,
    last_to_save,
    played,
)
from studio.pages.replays_list import ReplayShelf, last_duel_notice, saved_copy_notice
from studio.tasks import Work, WorkerContext
from studio.widgets import Button, GameClosedBlock, label


class GameOpen(RuntimeError):
    """The chosen installation must be closed for this write."""


class ChangedRecording(ValueError):
    """The library needs a new snapshot before this recording can be saved."""


def require_closed(game: Path) -> None:
    try:
        running = _game_running(game)
    except OSError as error:
        raise GameOpen("Couldn’t check if EDOPro is open. Close it, then choose Retry.") from error
    if running:
        raise GameOpen("EDOPro is open in the selected game folder.")


class ReplaysPage(ToolPage):
    id, title, icon = "replays", "Replays", "replays"
    mood = "replays"
    introduction = "Keep your last duel and find the ones worth watching again."

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.entries: tuple[ReplayEntry, ...] = ()
        self.selected: ReplayEntry | None = None
        self.last: ReplayEntry | None = None
        self.preserve: Button | None = None
        self.writing = False
        self.query, self.order = "", "Newest first"
        self.undo_notes: dict[str, tuple[ReplayNotes, ReplayNotes]] = {}
        self.saved: Path | None = None
        self.saved_copy: SavedCopy | None = None
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
                self._scan(game, passive=True)

        self.read(lambda _: folder_stamp(game), changed, lambda _: self._scan(game, passive=True))

    def installation_changed(self, installation: Installation) -> None:
        self.query, self.order = "", "Newest first"
        self.saved = self.saved_copy = None
        if not self.writing:
            super().installation_changed(installation)

    def refresh(self) -> None:
        self.stop_read()
        self.retry_back = None
        self.watch.stop()
        game = self.ctx.installation.path
        self.selected, self.entries = None, ()
        self.last = self.preserve = None
        if not game:
            self.no_game()
            return
        self.state_view(
            "loading",
            "Reading your replays…",
            "Looking for saved duels and your most recent recording.",
        )
        self._scan(Path(game))

    def _scan(self, game: Path, *, passive: bool = False) -> None:
        profile = Path(self.ctx.cfg.config_path).parent

        def scan(_: WorkerContext) -> tuple[tuple[int, ...], tuple[ReplayEntry, ...]]:
            if not game.is_dir():
                raise FileNotFoundError(
                    "The installation folder is unavailable. Reconnect the drive or choose another folder."
                )
            return folder_stamp(game), index_replays(game, annotations=AnnotationStore(profile))

        def failed(exc: Exception) -> None:
            self.watch.stop()
            self.state_view(
                "error",
                "Replays could not open",
                self.folder_read_error(),
                "Retry",
                self.refresh,
                technical=str(exc),
            )

        self.read(scan, lambda result: self._indexed(result, passive=passive), failed)

    def _indexed(
        self, result: tuple[tuple[int, ...], tuple[ReplayEntry, ...]], *, passive: bool
    ) -> None:
        selected = self._current_path() if passive and self.state == "library" else None
        focus = QApplication.focusWidget() if passive else None
        control = next(
            (
                name
                for name in ("search", "sort", "list", "reload")
                if focus is not None and focus is getattr(self, name, None)
            ),
            "",
        )
        self.stamp, self.entries = result
        self.watch.start()
        cursor = self.search.cursorPosition() if control == "search" else None
        self.library(focus=not passive)
        if self.state == "library":
            self._select_path(selected)
            if control:
                getattr(self, control).setFocus()
            if cursor is not None:
                self.search.setCursorPosition(cursor)
        elif passive and control == "reload":
            self.reload.setFocus()

    def library(self, *, focus: bool = True) -> None:
        self.selected = None
        self.surface("library")
        self.last = last_to_save(self.entries)
        self.preserve = None
        self.banner("Duel replays", self.introduction)
        if self.last:
            panel, self.preserve = last_duel_notice(played(self.last), self._preserve)
            self.body.addWidget(panel)
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
        shelf = ReplayShelf(self.query, self.order, bool(self.entries), self.refresh)
        self.reload = shelf.reload
        if not self.entries:
            self.state = "empty"
            self.body.addWidget(shelf)
            self.body.addStretch()
            if focus:
                self.reload.setFocus()
            return
        self.search, self.sort, self.list = shelf.search, shelf.sort, shelf.list
        self.count, self.no_matches = shelf.count, shelf.no_matches
        self.list.clicked.connect(self._select)
        self.list.activated.connect(self._select)
        self.search.textChanged.connect(self._filter)
        self.sort.currentTextChanged.connect(self._filter)
        self.body.addWidget(shelf, 1)
        shelf.show()
        self._filter()
        if focus:
            (self.preserve or self.search).setFocus()

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

    def _current_path(self) -> Path | None:
        index = self.list.currentIndex()
        return self.list.records.entries[index.row()].path if index.isValid() else None

    def _select_path(self, path: Path | None) -> bool:
        for row_index, entry in enumerate(self.list.records.entries):
            if entry.path == path:
                index = self.list.records.index(row_index)
                self.list.setCurrentIndex(index)
                self.list.scrollTo(index)
                return True
        return False

    def _select(self, index: QModelIndex) -> None:
        if self.state == "library" and index.isValid():
            self.detail(self.list.records.entries[index.row()])

    def _saved_copy(self) -> None:
        path = self.saved
        assert path is not None
        self.body.addWidget(
            saved_copy_notice(
                lambda: self._reveal(path),
                self._confirm_remove_saved if self.saved_copy is not None else None,
            )
        )

    def _confirm_remove_saved(self) -> None:
        if not self.saved_copy or not confirm_action(
            self,
            "Remove saved copy?",
            "Remove the copy you saved here. EDOPro’s last duel stays in place.",
            "Remove saved copy",
        ):
            return
        self._remove_saved(self.saved_copy)

    def _remove_saved(self, copy: SavedCopy) -> None:
        game = Path(self.ctx.installation.path)

        def remove(_: WorkerContext) -> None:
            require_closed(game)
            try:
                copy.remove(game)
            except ValueError as exc:
                raise ChangedRecording(str(exc)) from exc

        def done(_: object) -> None:
            self.saved = self.saved_copy = None
            self.refresh()
            self.ctx.notify("Saved replay copy removed.")

        self._write(
            "Removing the saved copy…",
            remove,
            done,
            lambda: self._remove_saved(copy),
            self.refresh,
            "The saved copy could not be removed",
        )

    def _preserve(self) -> None:
        entry = self.last
        if not entry:
            return
        game = Path(self.ctx.installation.path)

        def save(_: WorkerContext) -> tuple[Path, SavedCopy | None]:
            require_closed(game)
            if entry.replay_id is None:
                raise ChangedRecording("Refresh the library once the recording can be read.")
            existing = {path.name.casefold() for path in entry.path.parent.iterdir()}
            path = preserve_last_replay(game, source=entry.path, expected_id=entry.replay_id)
            if not path:
                raise ChangedRecording(
                    "The recording changed or could not be read. Refresh the library after the duel has finished."
                )
            # The engine can return an identical file that predates this operation.
            copy = (
                None if path.name.casefold() in existing else SavedCopy.check(path, entry.replay_id)
            )
            return path, copy

        def success(result: tuple[Path, SavedCopy | None]) -> None:
            self.saved, self.saved_copy = result
            self.refresh()

        self._write(
            "Saving your last duel…",
            save,
            success,
            self._preserve,
            self.refresh,
            "The last duel could not be saved",
        )

    def detail(self, entry: ReplayEntry) -> None:
        self.stop_read()
        self.retry_back = None
        self.selected = entry
        self.surface("detail")
        back = self.back_button(self._back)
        task_heading(self.body, "Replay details", played(entry) + "  ·  " + details_line(entry))
        prior = self.undo_notes.get(entry.replay_id or "")
        self.form = ReplayForm(entry, bool(prior and prior[0] == entry.annotation))
        self.body.addWidget(self.form, 1)
        self.form.arrange(self.width())
        if entry.replay_id:
            self.pin_footer(self.form.footer)
        self.form.reveal.clicked.connect(lambda: self._reveal(entry.path))
        self.form.export.clicked.connect(self._export)
        self.form.save.clicked.connect(lambda: self._store(self.form.draft()))
        self.form.undo.clicked.connect(self._undo)
        back.setFocus()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.state == "detail" and hasattr(self, "form"):
            self.form.arrange(self.width())

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

        self._write(
            "Saving replay details…",
            save,
            success,
            lambda: self._store(annotation, undo=undo),
            lambda: self._restore_draft(entry, annotation),
            "Replay details could not be saved",
        )

    def _restore_draft(self, entry: ReplayEntry, draft: ReplayNotes) -> None:
        self.detail(entry)
        self.form.name.setText(draft.label)
        self.form.tags.setText(format_tags(draft.tags))
        self.form.notes.setPlainText(draft.notes)

    def _export(self) -> None:
        entry = self.selected
        if not entry or entry.error:
            return
        profile = Path(self.ctx.cfg.config_path).parent
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export replay and notes",
            str(profile / "Duel with notes.zip"),
            "Replay ZIP (*.zip)",
        )
        if not destination:
            return
        path = Path(destination)
        if not path.suffix:
            path = path.with_suffix(".zip")
        self._export_to(entry, path)

    def _export_to(self, entry: ReplayEntry, path: Path) -> None:
        game = Path(self.ctx.installation.path)

        def export(_: WorkerContext) -> Path:
            if path.resolve().is_relative_to(game.resolve()):
                require_closed(game)
            return export_replay(entry, path)

        def success(_: Path) -> None:
            self.detail(entry)
            self.ctx.notify("Replay and notes exported.")

        self._write(
            "Exporting your replay…",
            export,
            success,
            lambda: self._export_to(entry, path),
            lambda: self.detail(entry),
            "Replay ZIP could not be exported",
        )

    def _reveal(self, path: Path) -> None:
        self.open_in_desktop(
            path,
            desktop_helpers.reveal_file,
            "The replay could not be shown. Refresh Replays if the file has moved.",
        )

    def _write(
        self,
        message: str,
        work: Work,
        done: Callable,
        retry: Callable,
        back: Callable,
        failure_title: str,
    ) -> None:
        if self.writing:
            return
        game = self.ctx.installation.path
        previous = "Replay details" if self.selected else self.title
        self.stop_read()
        self.writing = True
        self.ctx.set_busy(True)
        self.state_view(
            "busy",
            message,
            "You can change folders when this finishes.",
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
                blocked = isinstance(exc, GameOpen)
                if blocked:
                    self.surface("write_error")
                    self.back_button(back, previous)
                    block = GameClosedBlock()
                    self.body.addWidget(block)
                    self.body.addStretch()
                    block.retry.clicked.connect(retry)
                    block.show_reason(str(exc))
                    self.retry_back = back
                    return
                if stale:
                    explanation = str(exc)
                elif isinstance(exc, FileExistsError):
                    explanation = (
                        "A file already has that name. Choose another name for your export."
                    )
                else:
                    explanation = "Check that the folder is available and writable, then try again."
                self.state_view(
                    "write_error",
                    failure_title,
                    explanation,
                    "Refresh" if stale else "Retry",
                    self.refresh
                    if stale
                    else self._export
                    if isinstance(exc, FileExistsError)
                    else retry,
                    back=back,
                    technical="" if stale else str(exc),
                )
                self.retry_back = back

        job.result.connect(lambda value: done(value) if release() else None)
        job.error.connect(failed)

    def _may_leave(self) -> bool:
        if (
            self.state == "detail"
            and self.selected
            and self.form.draft() != self.selected.annotation
        ):
            return confirm_action(
                self,
                "Discard unsaved changes?",
                "Your changes to the label, tags and notes will be lost.",
                "Discard changes",
            )
        return True

    def _back(self) -> None:
        if self._may_leave():
            previous = self.selected
            self.library()
            if previous and self.state == "library" and self._select_path(previous.path):
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
        self.entries = ()
        self.selected = self.last = self.preserve = None
        self.surface("inactive")
        self.form = self.list = self.retry_back = None
        return super().hidden()

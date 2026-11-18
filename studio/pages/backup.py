"""Back up, compare, and restore the selected EDOPro installation."""

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TypeVar
from weakref import ref

from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QFileDialog, QScrollArea, QVBoxLayout

import backup
import desktop_helpers
from backup.core import Progress
from music.settings import _game_running
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.backup_data import Mode, Review, history
from studio.pages.backup_jobs import (
    WriteError,
    create_archive,
    prepare_comparison,
    prepare_restore,
    run_write,
)
from studio.pages.backup_results import BusyScreen, ErrorScreen, RestoredScreen, SavedScreen
from studio.pages.backup_review import CompareScreen, PreviewScreen
from studio.pages.backup_screens import (
    UNDO_DESCRIPTION,
    ArchiveScreen,
    HomeScreen,
    SaveScreen,
    Screen,
)
from studio.pages.base import Page
from studio.pages.health_layout import confirm_action, tool_margins
from studio.tasks import Job, WorkerContext
from studio.widgets import margins

T = TypeVar("T")
BACKUP_FILTER = "EDOPro backups (*.zip);;All files (*)"


class BackupPage(Page):
    id, title, icon = "backup", "Backup & transfer", "backup"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.frame = QVBoxLayout(self)
        margins(self.frame, 0)
        self.screen: Screen | None = None
        self.job: Job | None = None
        self.writing = self.closing = False
        self.back: Callable[[], None] | None = None
        self.selected = backup.DEFAULTS
        self.archive: Path | None = None
        self.manifest: backup.Manifest | None = None
        self.plan: backup.Plan | None = None
        self.compare = False

    def shown(self) -> None:
        self.show_home()

    def hidden(self) -> bool:
        if self.writing:
            return False
        self._discard_read()
        self.archive = self.manifest = self.plan = None
        self._remove_screen()
        self.screen = self.back = None
        return True

    def installation_changed(self, installation: Installation) -> None:
        if self.isVisible() and not self.writing:
            self.show_home()

    def request_close(self) -> bool:
        self.closing = True
        if self.job:
            self.job.cancel()
        return True

    def escape(self) -> bool:
        if self.writing:
            self.progress.cancel.setFocus()
        elif (
            isinstance(self.screen, (PreviewScreen, CompareScreen))
            and self.screen.grouped.close_details()
        ):
            return True
        elif self.back:
            self.back()
        else:
            return False
        return True

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._size_screen()

    def _size_screen(self) -> None:
        compact = self.height() < t.MIN_SIZE[1]
        if self.screen:
            tool_margins(self.screen.body, self.width())
            self.screen.body.setSpacing(t.MD if compact else t.LG)
        if isinstance(self.screen, (SaveScreen, PreviewScreen, CompareScreen)):
            self.screen.compact(compact)
        if isinstance(self.screen, (SaveScreen, PreviewScreen)) and compact:
            self.screen.body.setContentsMargins(t.LG, t.MD, t.LG, t.MD)
            self.screen.body.setSpacing(t.SM)
        if self.screen and self.screen.fixed_footer:
            inset = self.screen.body.contentsMargins().left()
            self.screen.fixed_footer.layout().setContentsMargins(
                inset, 0, inset, t.MD if compact else t.XL
            )
        if isinstance(self.screen, HomeScreen):
            ratio = 0.47 if self.screen.has_installation else 0.65
            height = t.HERO_MIN if self.screen.pending else int(self.height() * ratio)
            if self.screen.has_installation and not self.screen.ready:
                height -= t.TILE_H
            self.screen.hero.setFixedHeight(max(t.HERO_MIN, min(t.HERO_MAX, height)))
            size = t.DISPLAY - t.XS if compact else t.HUGE
            self.screen.hero.headline.setStyleSheet(f"font-size: {size}px;")

    @property
    def scroll(self) -> QScrollArea:
        assert self.screen is not None
        return self.screen.scroll

    def _remove_screen(self) -> None:
        if self.screen:
            self.frame.removeWidget(self.screen)
            self.screen.hide()
            self.screen.deleteLater()

    def _show(self, screen: Screen, back: Callable[[], None] | None = None) -> None:
        self._remove_screen()
        self.screen, self.back = screen, back
        self._size_screen()
        self.frame.addWidget(screen)
        screen.show()
        if screen.focus and screen.focus.isEnabled():
            screen.focus.setFocus()

    def _install(self) -> Callable[[], backup.Installation]:
        game = Path(self.ctx.installation.path)
        config_path = Path(self.ctx.cfg.config_path)
        return lambda: backup.Installation.at(game, config_path)

    def _discard_read(self) -> None:
        if self.job and not self.writing:
            self.job.cancel()
            self.job = None

    def _start(
        self,
        work: Callable[[WorkerContext], T],
        result: Callable[[T], None],
        failure: Callable[[Exception], None],
        *,
        write: bool = False,
    ) -> Job:
        self._discard_read()
        self.writing = write
        if write:
            self.ctx.set_busy(True)
        job = self.ctx.tasks.start(work)
        self.job = job
        pending = ref(job)

        def deliver(callback: Callable, value: object) -> None:
            if self.job is not None and self.job is pending():
                self.job = None
                if write:
                    self.writing = False
                    self.ctx.set_busy(False)
                if not self.closing:
                    callback(value)

        job.result.connect(lambda value: deliver(result, value))
        job.error.connect(lambda error: deliver(failure, error))
        return job

    def show_home(self) -> None:
        self._discard_read()
        self.selected = backup.DEFAULTS
        self.archive = self.manifest = self.plan = None
        current = self.ctx.installation
        screen = HomeScreen(
            current.path,
            current.ready,
            self.show_save,
            self.choose_backup,
            self.ctx.installations_requested.emit,
            self.confirm_recovery,
        )
        self._show(screen)
        if current.path:
            install = self._install()

            def ready(records: tuple[backup.Transaction, ...]) -> None:
                screen.set_history(records, "", self.show_home, self._preview_undo)
                self._size_screen()
                if screen.compare.hasFocus() and screen.save.isEnabled():
                    screen.save.setFocus()

            self._start(
                lambda _: history(install()),
                ready,
                lambda _: screen.set_history(
                    (),
                    "Couldn’t read restore history. Check that the folder is available.",
                    self.show_home,
                    self._preview_undo,
                ),
            )

    def _loading(self, title: str, description: str, back: Callable[[], None]) -> None:
        self._show(BusyScreen(title, description, back, writing=False), back)

    def show_save(self) -> None:
        install = self._install()
        self._loading("Save a backup", "Counting your personal files…", self.show_home)
        self._start(
            lambda worker: backup.inventory(install(), cancel=worker.cancel.is_set),
            lambda items: self._show(
                SaveScreen(items, self.show_home, self.save, self.selected), self.show_home
            ),
            lambda error: self._error(
                WriteError(error), "Couldn’t read your files", self.show_home, self.show_save
            ),
        )

    def save(self, categories: tuple[str, ...]) -> None:
        self.selected = categories
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Save your EDOPro backup",
            "EDOPro backup.zip",
            BACKUP_FILTER,
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not destination:
            return
        path = Path(destination)
        if not path.suffix:
            path = path.with_suffix(".zip")
        install = self._install()

        self._write(
            "Saving your backup",
            "Copying and checking the selected items. Your EDOPro files stay in place.",
            lambda worker, report: create_archive(install(), path, categories, worker, report),
            lambda saved: self._saved(*saved),
            "Couldn’t save your backup",
            self.show_save,
            lambda: self.save(categories),
        )

    def _saved(self, path: Path, manifest: backup.Manifest) -> None:
        self._show(
            SavedScreen(path, manifest, self.show_home, partial(self._reveal, path)),
            self.show_home,
        )

    def _reveal(self, path: Path) -> None:
        message = "Couldn’t open the backup folder. Its location is shown above."
        job = self.ctx.tasks.start(lambda _: desktop_helpers.open_link(path.parent))
        job.result.connect(lambda opened: None if opened else self.ctx.notify(message))
        job.error.connect(lambda _: self.ctx.notify(message))

    def choose_backup(self, compare: bool) -> None:
        title = "Choose a backup to compare" if compare else "Choose the backup to restore"
        filename, _ = QFileDialog.getOpenFileName(self, title, "", BACKUP_FILTER)
        if filename:
            self.inspect_backup(Path(filename), compare)

    def inspect_backup(self, path: Path, compare: bool) -> None:
        self.archive, self.compare = path, compare
        self.manifest = self.plan = None
        self._loading("Open a backup", "Reading the backup summary…", self.show_home)

        def ready(manifest: backup.Manifest) -> None:
            self.manifest = manifest
            self.show_archive()

        self._start(
            lambda _: backup.inspect(path),
            ready,
            lambda error: self._error(
                WriteError(error),
                "Couldn’t open this backup",
                self.show_home,
                lambda: self.choose_backup(compare),
                retry_label="Choose another backup",
                path=str(path),
            ),
        )

    def show_archive(self) -> None:
        self._discard_read()
        assert self.archive is not None and self.manifest is not None
        self._show(
            ArchiveScreen(
                str(self.archive),
                self.manifest,
                self.compare,
                self.show_home,
                lambda: self.choose_backup(self.compare),
                self.show_comparison if self.compare else self.show_preview,
            ),
            self.show_home,
        )

    def show_preview(self, policy: backup.Policy = "keep_mine") -> None:
        assert self.archive is not None
        screen = PreviewScreen(
            str(self.archive),
            self.ctx.installation.path,
            self.show_archive,
            self._plan,
            self.confirm_restore,
        )
        self._show(screen, self.show_archive)
        if policy == "keep_mine":
            self._plan(policy)
        else:
            screen.policy.select({"take_theirs": 1, "keep_both": 2}[policy])

    def _plan(self, policy: backup.Policy) -> None:
        install, archive = self._install(), self.archive
        assert archive is not None and isinstance(self.screen, PreviewScreen)
        screen = self.screen
        expected = self.manifest
        assert expected is not None
        self.plan = None
        screen.set_loading()

        def ready(value: tuple[backup.Plan, Review]) -> None:
            self.plan, review = value
            screen.show_review(review)

        self._start(
            lambda worker: prepare_restore(install(), archive, expected, policy, worker),
            ready,
            lambda error: self._error(
                WriteError(error),
                "Couldn’t preview this restore",
                self.show_archive,
                lambda: (
                    self.inspect_backup(archive, False)
                    if "Backup changed" in str(error)
                    else self.show_preview(policy)
                ),
            ),
        )

    def show_comparison(self) -> None:
        install, archive = self._install(), self.archive
        expected = self.manifest
        assert archive is not None and expected is not None
        self._loading("Compare with a backup", "Comparing your files…", self.show_archive)

        self._start(
            lambda worker: prepare_comparison(install(), archive, expected, worker),
            lambda review: self._show(
                CompareScreen(str(archive), review, self.show_archive), self.show_archive
            ),
            lambda error: self._error(
                WriteError(error),
                "Couldn’t compare the files",
                self.show_archive,
                lambda: self.inspect_backup(archive, True),
            ),
        )

    def confirm_restore(self) -> None:
        plan = self.plan
        if not plan or self.ctx.busy:
            return
        count = sum(item.action != "skip" for item in plan.items)
        change = "1 change" if count == 1 else f"{count:,} changes"
        if count and confirm_action(
            self,
            f"Apply {change}?",
            f"Review the proposed changes before restoring to {plan.install.game.name}. "
            "Nothing has been changed yet.",
            f"Restore to {plan.install.game.name}",
        ):
            self.run_restore(plan)

    def run_restore(self, plan: backup.Plan) -> None:
        def restore(worker: WorkerContext, report: Progress) -> backup.Transaction:
            self._require_closed(plan.install)
            return backup.execute(
                plan, progress=report, cancel=worker.cancel.is_set, require_closed=True
            )

        self._write(
            "Restoring your backup",
            "Keep EDOPro closed until this finishes.",
            restore,
            self._restored,
            "Couldn’t finish restoring",
            lambda: self.show_preview(plan.policy),
            lambda: self.run_restore(plan),
            transaction_install=lambda: plan.install,
            refresh_preview=lambda: self.show_preview(plan.policy),
        )

    def confirm_recovery(self, tx: backup.Transaction, mode: Mode) -> None:
        if self.ctx.busy:
            return
        title = "Finish this restore?" if mode == "finish" else "Put back the previous files?"
        message = (
            "Apply the remaining changes from the interrupted restore."
            if mode == "finish"
            else UNDO_DESCRIPTION
        )
        if mode == "finish":
            affirmative = "Finish restore"
        elif tx.status == "rolling_back":
            affirmative = "Continue undo"
        else:
            affirmative = (
                "Undo this restore" if tx.status == "complete" else "Undo interrupted restore"
            )
        if confirm_action(self, title, message, affirmative):
            self.run_recovery(tx, mode)

    def run_recovery(self, tx: backup.Transaction, mode: Mode) -> None:
        install = self._install()

        def recover(worker: WorkerContext, report: Progress) -> backup.Transaction:
            current = install()
            self._require_closed(current)
            return backup.recover(
                current,
                tx.id,
                mode,
                progress=report,
                cancel=worker.cancel.is_set,
                require_closed=True,
            )

        self._write(
            "Finishing the restore" if mode == "finish" else "Putting back your previous files",
            "Keep EDOPro closed until this finishes.",
            recover,
            self._restored,
            "Couldn’t finish this change",
            self.show_home,
            lambda: self.run_recovery(tx, mode),
            transaction_install=install,
        )

    @staticmethod
    def _require_closed(install: backup.Installation) -> None:
        if _game_running(install.game):
            raise backup.GameRunning()

    def _restored(self, transaction: backup.Transaction) -> None:
        self.plan = self.manifest = self.archive = None
        screen = RestoredScreen(
            transaction,
            self.show_home,
            partial(self.confirm_recovery, transaction, "rollback"),
        )
        self._show(screen, self.show_home)
        if transaction.status == "complete":
            self._preview_undo(transaction, screen.show_undo)

    def _preview_undo(
        self, transaction: backup.Transaction, ready: Callable[[backup.UndoPreview], None]
    ) -> None:
        install = self._install()

        def check(worker: WorkerContext) -> backup.UndoPreview:
            current = install()
            preview = backup.preview_undo(
                current, transaction.id, cancel=worker.cancel.is_set, require_closed=True
            )
            if preview.target:
                try:
                    return replace(preview, target=str(current.target(preview.target)))
                except (OSError, ValueError):
                    return preview
            return preview

        self._start(
            check,
            ready,
            lambda error: ready(backup.UndoPreview(False, str(error))),
        )

    def _write(
        self,
        title: str,
        description: str,
        work: Callable[[WorkerContext, Progress], T],
        result: Callable[[T], None],
        failure_title: str,
        back: Callable[[], None],
        retry: Callable[[], None],
        *,
        transaction_install: Callable[[], backup.Installation] | None = None,
        refresh_preview: Callable[[], None] | None = None,
    ) -> None:
        if self.ctx.busy:
            return
        screen = BusyScreen(title, description, self._cancel, writing=True)
        self.progress = screen.progress
        self._show(screen)

        def failed(problem: WriteError) -> None:
            stale = refresh_preview and "preview is stale" in str(problem.error)
            next_attempt = refresh_preview if stale else retry
            if (
                refresh_preview
                and problem.interrupted
                and isinstance(problem.error, backup.GameRunning)
            ):
                next_attempt = partial(self.run_recovery, problem.interrupted, "finish")
            self._error(
                problem,
                failure_title,
                back,
                next_attempt,
                retry_label="Refresh preview" if stale else "Retry",
            )

        def finished(value: T | WriteError) -> None:
            if isinstance(value, WriteError):
                failed(value)
            else:
                result(value)

        job = self._start(
            lambda worker: run_write(worker, work, transaction_install),
            finished,
            lambda error: failed(WriteError(error)),
            write=True,
        )
        job.progress.connect(self._progress)

    def _progress(self, event: tuple[int, int, str]) -> None:
        done, total, path = event
        self.progress.phase.setText("Checking and copying files")
        self.progress.advance_to(done, total)
        if self.progress.cancel.isEnabled():
            self.progress.detail.setText(path)

    def _cancel(self) -> None:
        if self.job and self.writing:
            self.job.cancel()
            self.progress.cancel.setEnabled(False)
            self.progress.detail.setText("Canceling at the next safe point…")

    def _error(
        self,
        problem: WriteError,
        title: str,
        back: Callable[[], None],
        retry: Callable[[], None],
        *,
        retry_label: str = "Retry",
        path: str = "",
    ) -> None:
        if problem.history_unavailable:
            retry_label, retry = "Check history", self.show_home
        if problem.interrupted:
            back = self.show_home
        return_to = {
            self.show_home: self.title,
            self.show_archive: "Backup contents",
            self.show_save: "Save a backup",
        }.get(back, "Restore preview")
        self._show(
            ErrorScreen(
                problem, title, back, retry, retry_label, path, self.confirm_recovery, return_to
            ),
            back,
        )

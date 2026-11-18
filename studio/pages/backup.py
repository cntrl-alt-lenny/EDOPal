"""Back up, compare, and restore the selected EDOPro installation."""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QResizeEvent
from PySide6.QtWidgets import QFileDialog, QScrollArea, QVBoxLayout

import backup
from backup.core import Progress
from music.settings import _game_running
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.backup_data import (
    Mode,
    Review,
    check_destination,
    comparison_rows,
    history,
    restore_review,
)
from studio.pages.backup_jobs import WriteError, run_write
from studio.pages.backup_results import BusyScreen, ErrorScreen, RestoredScreen, SavedScreen
from studio.pages.backup_screens import (
    UNDO_DESCRIPTION,
    ArchiveScreen,
    CompareScreen,
    HomeScreen,
    PreviewScreen,
    SaveScreen,
    Screen,
)
from studio.pages.base import Page
from studio.tasks import Job, WorkerContext
from studio.widgets import margins

T = TypeVar("T")
BACKUP_FILTER = "EDOPro backups (*.zip);;All files (*)"


class BackupPage(Page):
    id, title, icon = "backup", "Backup && transfer", "backup"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        layout = QVBoxLayout(self)
        margins(layout, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll)
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
            self._cancel()
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
            margins(self.screen.body, t.LG if compact else t.XXL)
            self.screen.body.setSpacing(t.MD if compact else t.LG)
        if isinstance(self.screen, (PreviewScreen, CompareScreen)):
            self.screen.grouped.compact(compact)
        if isinstance(self.screen, HomeScreen):
            ratio = 0.47 if self.screen.has_installation else 0.65
            height = t.HERO_MIN if self.screen.pending else int(self.height() * ratio)
            if self.screen.has_installation and not self.screen.ready:
                height -= t.TILE_H
            self.screen.hero.setFixedHeight(max(t.HERO_MIN, min(t.HERO_MAX, height)))
            size = t.DISPLAY - t.XS if compact else t.HUGE
            self.screen.hero.headline.setStyleSheet(f"font-size: {size}px;")

    def _show(self, screen: Screen, back: Callable[[], None] | None = None) -> None:
        previous = self.scroll.takeWidget()
        if previous:
            previous.deleteLater()
        self.screen, self.back = screen, back
        self._size_screen()
        self.scroll.setWidget(screen)
        self.scroll.verticalScrollBar().setValue(0)
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

        def deliver(callback: Callable, value: object) -> None:
            if self.job is job:
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
                screen.set_history(records, "", self.show_home)
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

        def create(worker: WorkerContext, report: Progress) -> backup.Manifest:
            target = install()
            return backup.create(
                target,
                check_destination(path, target),
                categories,
                progress=report,
                cancel=worker.cancel.is_set,
            )

        self._write(
            "Saving your backup",
            "Copying and checking the selected items. Your EDOPro files stay in place.",
            create,
            lambda manifest: self._saved(path, manifest),
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
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
            self.ctx.notify("Couldn’t open the backup folder. Its location is shown above.")

    def choose_backup(self, compare: bool) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose a backup", "", BACKUP_FILTER)
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
            str(self.archive), self.show_archive, self._plan, self.confirm_restore
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
        self.plan = None
        screen.set_loading()

        def read(worker: WorkerContext) -> tuple[backup.Plan, Review]:
            cancel = worker.cancel.is_set
            plan = backup.plan_restore(archive, install(), policy, cancel=cancel)
            if plan.manifest != expected:
                raise ValueError("Backup changed after preview")
            return plan, restore_review(plan, cancel)

        def ready(value: tuple[backup.Plan, Review]) -> None:
            self.plan, review = value
            screen.show_review(review)

        self._start(
            read,
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
        assert archive is not None
        self._loading("Compare with a backup", "Comparing your files…", self.show_archive)

        def compare(worker: WorkerContext) -> Review:
            cancelled = worker.cancel.is_set
            differences = backup.compare(install(), archive, cancel=cancelled)
            if backup.inspect(archive) != expected:
                raise ValueError("Backup changed during comparison")
            return comparison_rows(differences, cancelled)

        self._start(
            compare,
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
        change = "1 reviewed change" if count == 1 else f"{count:,} reviewed changes"
        if count and self.ctx.confirm(
            "Restore to this installation?",
            f"Apply {change} to {plan.install.game.name}? "
            "Any files you chose to replace can be put back using Undo this restore.",
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
            "Keep EDOPro closed. The selected installation is locked until this ends.",
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
        if self.ctx.confirm(title, message):
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
            "Keep EDOPro closed. The selected installation is locked until this ends.",
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
        self._show(
            RestoredScreen(
                transaction,
                self.show_home,
                partial(self.confirm_recovery, transaction, "rollback"),
            ),
            self.show_home,
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
            self.progress.detail.setText("Stopping safely…")

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
        self._show(
            ErrorScreen(problem, title, back, retry, retry_label, path, self.confirm_recovery),
            back,
        )

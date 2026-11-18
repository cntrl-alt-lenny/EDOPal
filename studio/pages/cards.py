import os
from copy import copy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Slot

from artwork_flow import (
    ArtworkStorageError,
    Summary,
    coverage_summary,
    make_run_config,
    sync_summary,
    valid_decks,
)
from config import Config
from main import DownloadStats
from studio.context import AppContext, GameOpenError, Installation
from studio.pages.card_results import player_summary, retry_summary
from studio.pages.card_stage import CardStage
from studio.pages.cards_library import LibrarySnapshot, inspect_library
from studio.pages.cards_undo import remember_changes, restore_artwork
from studio.tasks import FolderRequest, Job, WorkerContext


class CardsPage(CardStage):
    id, title, icon = "cards", "HD Cards", "cards"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.job: Job | None = None
        self.summary: Summary | None = None
        self.total = self.done = 0
        self.folder_cancelled = False
        self.scan: Job | None = None
        self.origin = "options"
        self.stale_coverage = False
        self.no_decks = False
        self.retry_artwork: tuple[set[int], set[int]] | None = None
        self.retry_label = ""
        self.block_reason = ""
        self.restoring = False
        self.retry_args: tuple[Path | None, tuple[Path, ...]] = (None, ())
        self.guard.retry.clicked.connect(self._retry)
        self.back.clicked.connect(self._back_to_origin)
        self.results.retry.clicked.connect(self._retry)
        self.results.back.clicked.connect(self._back_to_origin)
        self.results.decks.clicked.connect(lambda: context.navigate("decks"))
        self.results.full_library.clicked.connect(self._sync_everything)
        self.options.check_again.clicked.connect(
            lambda: context.select_installation_requested.emit(context.installation.path)
        )
        self.options.connect_game.clicked.connect(lambda: context.installations_requested.emit())
        self.options.start.clicked.connect(self.start_sync)
        self.options.settings.clicked.connect(lambda: self.open_view("settings"))
        for button in (self.options.coverage, self.settings.coverage):
            button.clicked.connect(self.show_coverage)
        self.settings.done.clicked.connect(
            lambda: context.save_card_preferences(self.settings.preferences(), self._close_settings)
        )
        context.preferences_changed.connect(self._load_defaults)
        context.busy_changed.connect(self.settings.setDisabled)
        context.busy_changed.connect(self.settings.actions.setDisabled)
        self.settings.back.clicked.connect(self._discard_settings)
        self.results.done.clicked.connect(self._back_to_origin)
        self.results.failures.clicked.connect(self._reveal_failures)
        self.options.undo.clicked.connect(self._confirm_restore)
        self.results.undo.clicked.connect(self._confirm_restore)
        self.progress.cancel.clicked.connect(self.cancel)
        self.options.scope.changed.connect(self._scope_status)

    def installation_changed(self, installation: Installation) -> None:
        if self.job and self.run_config.stats:
            self.stale_coverage = True
            self.job.cancel()
            self.open_view("options")
        self.options.start.setVisible(installation.ready)
        self.options.connect_game.setVisible(not installation.path)
        self.options.check_again.setVisible(bool(installation.path) and not installation.ready)
        self.options.selection.setVisible(bool(installation.path))
        self.options.scope.setVisible(bool(installation.path))
        self.options.start.setEnabled(installation.ready)
        self.options.coverage.setEnabled(installation.ready)
        self.options.scope.setEnabled(installation.ready)
        self.settings.coverage.setEnabled(installation.ready)
        self._load_scope()
        self._snapshot(installation)
        self._scope_status()
        if not self.job and self.state in ("results", "blocked"):
            self.open_view("options")

    def _load_scope(self) -> None:
        cfg = self.ctx.cfg
        decks = cfg.studio_scope == "decks" or (
            cfg.studio_scope == "auto" and (self.ctx.installation.recommend_decks or cfg.my_decks)
        )
        self.options.scope.select(int(decks))

    def _load_defaults(self) -> None:
        self.settings.load(self.ctx.cfg)
        self._load_scope()
        if not self.restoring and (
            self.state == "blocked" or (self.state == "results" and self.retry_label)
        ):
            # Changed defaults invalidate the blocked operation's original inputs.
            self.open_view("options")

    def _close_settings(self) -> None:
        self.open_view("options")
        self.options.settings.setFocus()

    def _discard_settings(self) -> None:
        if self.settings.discard(self.ctx):
            self._close_settings()

    def _back_to_origin(self) -> None:
        self.open_view(self.origin)
        if self.origin == "settings":
            self.settings.coverage.setFocus()
        elif self.restoring and self.options.undo.isVisible():
            self.options.undo.setFocus()
        elif getattr(self, "run_config", None) and self.run_config.stats:
            self.options.coverage.setFocus()

    def _sync_everything(self) -> None:
        self.options.scope.select(0)
        self.start_sync()

    def _history_key(self) -> str:
        path = os.path.normcase(self.ctx.installation.path)
        return "cards/last/" + sha256(path.encode()).hexdigest()

    def _snapshot(self, installation: Installation) -> None:
        if self.scan:
            self.scan.cancel()
            self.scan = None
        self.results.undo.hide()
        self.options.undo.hide()
        self.options.snapshot.setVisible(installation.ready)
        if not installation.ready:
            return
        self.options.last.setText(
            self.ctx.state.value(
                self._history_key(), "Last sync   ·   No runs recorded yet.", type=str
            )
        )
        for value in self.options.values:
            value.setText("…")
        self.options.captions[0].setText("Reading artwork…")
        cfg = copy(self.ctx.cfg)
        self.scan = job = self.ctx.tasks.start(lambda worker: inspect_library(cfg, worker.cancel))

        job.result.connect(self._display_snapshot)
        job.error.connect(self._scan_failed)

    @Slot(object)
    def _display_snapshot(self, result: LibrarySnapshot | None) -> None:
        if self.scan is not self.sender() or result is None:
            return
        for label, value in zip(
            self.options.values, (result.artwork, f"{result.decks:,}", result.storage), strict=True
        ):
            label.setText(value)
        self.options.captions[0].setText(result.caption)
        self.options.undo.setVisible(result.can_restore)
        self.results.undo.setVisible(
            result.can_restore and self.state == "results" and self.results.allows_undo
        )

    @Slot(object)
    def _scan_failed(self, error: Exception) -> None:
        if self.scan is self.sender():
            for value in self.options.values:
                value.setText("—")
            self.options.captions[0].setText("Could not read library")
            if isinstance(error, OSError):
                self.ctx.folder_unavailable.emit(self.ctx.installation.path)

    def _scope_status(self) -> None:
        install = self.ctx.installation
        if not install.path:
            message = "Choose your EDOPro folder to enable artwork sync."
        elif not install.ready:
            message = (
                "Finish setting up EDOPro. Let its card databases update, then choose Check again."
            )
        elif install.recommend_decks and self.options.scope.group.checkedId() == 1:
            message = "My decks is recommended here. New art appears next time you start EDOPro."
        else:
            message = "New artwork appears the next time you start EDOPro."
        self.options.status.setText(message)

    @Slot()
    def start_sync(self) -> None:
        self._launch(False)

    @Slot()
    def show_coverage(self) -> None:
        self._launch(True)

    def sync_deck_files(self, game_path: Path, paths: tuple[Path, ...]) -> bool:
        """Schedule a validated, isolated deck-only sync. Errors appear inline."""
        if self.ctx.busy or not self.ctx.installation.ready or not self.ctx.navigate(self.id):
            return False
        if not paths:
            self.ctx.notify("Choose at least one saved deck.")
            return False
        return self._launch(False, game_path, tuple(paths))

    def _launch(
        self,
        stats_only: bool,
        game_path: Path | None = None,
        paths: tuple[Path, ...] = (),
        *,
        retry: bool = False,
    ) -> bool:
        if self.job or self.ctx.busy or not self.ctx.installation.ready:
            return False
        picks = {key: switch.isChecked() for key, switch in self.settings.toggles.items()}
        picks.update(my_decks=self.options.scope.group.checkedId() == 1, stats=stats_only)
        self.block_reason = ""
        self.restoring = False
        self.retry_args = game_path, paths
        self.imported_run = bool(paths)
        self.run_config = cfg = make_run_config(self.ctx.cfg, picks, self.ctx.apply_choices, paths)
        if not retry:
            self.retry_artwork = None
        self.retry_label = ""
        if self.retry_artwork:
            cfg.retry_artwork = self.retry_artwork
            cfg.theme = cfg.audio = cfg.prune = cfg.textures = False
        cfg.update_checked_by_shell = True
        if not stats_only:
            self.ctx.set_busy(True)
        if self.state in ("options", "settings"):
            self.origin = self.state
        self.stale_coverage = self.no_decks = False
        self.progress.cancel.setText("Cancel coverage check" if stats_only else "Cancel sync")
        self.ctx.exit_code = self.done = self.total = 0
        self.summary = None
        self.folder_cancelled = False
        scope = "Full library" if not cfg.my_decks else "My decks"
        if paths:
            scope = "Imported decks"
        self.banner.description.setText(
            "Reading artwork already on disk."
            if stats_only
            else f"Updating artwork for {scope}… You can change folders when this finishes."
        )
        self.progress.begin(
            "Measuring artwork coverage…" if stats_only else "Reading card databases…"
        )
        if paths:
            self.progress.detail.setText(f"Artwork for {len(paths)} imported deck(s)")
        self.open_view("progress")

        def prepare() -> None:
            if not stats_only:
                self.ctx.require_game_closed(Path(cfg.edopro_path))
                pick = cfg.folder_picker

                def checked_folder(initial: str) -> str | None:
                    replacement = pick(initial)
                    if replacement:
                        self.ctx.require_game_closed(Path(replacement))
                    return replacement

                cfg.folder_picker = checked_folder
            if game_path is not None and not valid_decks(cfg.edopro_path, game_path, paths):
                raise ValueError("Choose saved decks in the selected installation’s deck folder.")

        run = self.ctx.run_coro

        async def reversible(job_cfg: Config) -> Any:
            # Injected workers (including the offline demo) share the production journal.
            stats = None
            try:
                with remember_changes(job_cfg):
                    stats = await run(job_cfg)
            except ArtworkStorageError as exc:
                if stats is None:
                    stats = DownloadStats()
                stats.storage_error = stats.storage_error or str(exc)
            return stats

        self.job = self.ctx.tasks.sync(cfg, run if stats_only else reversible, prepare)
        self.job.progress.connect(self._event)
        self.job.result.connect(self._result)
        self.job.error.connect(self._error)
        self.job.finished.connect(self._finish)
        return True

    @Slot(object)
    def _event(self, event: tuple) -> None:
        kind = event[0]
        cancelling = self.job is not None and self.job.cancel_event.is_set()
        if kind == "task":
            self.total, self.done = event[3], 0
            if not cancelling:
                self.progress.phase.setText(event[2])
            self.progress.advance_to(self.done, self.total)
        elif kind == "adv":
            self.done += event[2]
            self.progress.advance_to(self.done, self.total)
        elif kind == "desc" and not cancelling:
            self.progress.detail.setText(event[2])
        elif kind == "notice":
            # The engine also announces its idle outcome to console-style progress sinks.
            # The receipt already displays that heading in the window.
            idle = getattr(self.run_config, "idle_summary", None)
            if not idle or event[1] != idle[0]:
                self.ctx.notify(event[1])
        elif kind == "coverage":
            self.summary = coverage_summary(event[1])
        elif kind == "folder":
            request: FolderRequest = event[1]
            path = None
            try:
                can_pick = not self.run_config.stats or (self.isVisible() and not self.ctx.busy)
                if can_pick and not cancelling and not self.imported_run and not self.retry_artwork:
                    path = self.ctx.choose_folder(request.initial)
            finally:
                self.folder_cancelled = path is None
                request.answer(path)

    @Slot(object)
    def _result(self, stats: Any) -> None:
        if self.summary is None:
            self._make_summary({"stats": stats})
            idle = getattr(self.run_config, "idle_summary", None)
            self.no_decks = bool(
                idle
                and self.summary.title
                in {
                    "No decks found — nothing to sync",
                    "No cards found in your decks — nothing to sync",
                }
            )
            if stats is not None:
                self.summary, self.retry_label = retry_summary(self.summary, stats)
                if self.retry_label and not stats.storage_error:
                    self.retry_artwork = stats.transient_ids, stats.field_transient_ids

    @Slot(object)
    def _error(self, exc: BaseException) -> None:
        if isinstance(exc, GameOpenError):
            self.block_reason = str(exc)
            return
        if isinstance(exc, SystemExit):
            self.ctx.exit_code = exc.code if isinstance(exc.code, int) else 1
            self._make_summary(
                {"exit": self.ctx.exit_code, "error": getattr(exc, "user_message", None)}
            )
        else:
            self.ctx.exit_code = 1
            self._make_summary({"error": f"{type(exc).__name__}: {exc}"})

    def _make_summary(self, payload: dict) -> None:
        self.summary = sync_summary(
            self.run_config,
            payload,
            self.job.cancel_event.is_set(),
            self.folder_cancelled,
            self.run_config.stats,
        )

    @Slot()
    def _finish(self) -> None:
        self.job = None
        cfg = self.run_config
        if cfg.stats and self.stale_coverage:
            return
        if not cfg.stats:
            self.ctx.cfg.saved_original_text = cfg.saved_original_text
            self.ctx.cfg.era_art_applied = cfg.era_art_applied
        if cfg.edopro_path != self.ctx.installation.path:
            # The backend only adopts a replacement after validating its databases.
            self.ctx.set_installation(Installation(cfg.edopro_path, ready=True))
        if not cfg.stats:
            self.ctx.set_busy(False)
        if self.block_reason:
            self.open_view("blocked")
            self.guard.show_reason(self.block_reason)
            return
        self.summary = player_summary(self.summary)
        if not cfg.stats and not self.no_decks and self.summary:
            when = datetime.now().strftime("%d %b %Y at %H:%M")
            detail = self.summary.title
            if self.summary.first[1] == "Downloaded":
                detail += f" · {self.summary.first[0]} downloaded"
            self.ctx.state.setValue(self._history_key(), f"Last sync   ·   {when}   ·   {detail}")
            self._snapshot(self.ctx.installation)
        self.results.undo.hide()
        scope = "My decks" if cfg.my_decks and not cfg.stats else "Full library"
        self.results.show_result(
            self.summary,
            no_decks=self.no_decks,
            retry=self.retry_label,
            back_to="Sync settings" if self.origin == "settings" else "HD Cards",
            coverage=cfg.stats,
            scope="Selected decks" if self.imported_run else scope,
        )
        self.open_view("results")
        if self.no_decks and self.isVisible():
            self.results.decks.setFocus()

    def _retry(self) -> None:
        if self.restoring:
            self._restore()
        else:
            self._launch(False, *self.retry_args, retry=True)

    def _confirm_restore(self) -> None:
        if self.ctx.confirm(
            "Undo the last artwork change?",
            "Put back the card art, playmat art and theme files replaced by that sync, and remove files it added. "
            "Files edited since the sync are protected. The change takes effect next time you start EDOPro.",
        ):
            self._restore()

    def _restore(self) -> None:
        if self.ctx.busy or not self.ctx.installation.path:
            return
        self.restoring = True
        self.origin = "options"
        self.ctx.set_busy(True)
        self.progress.begin("Restoring previous artwork…")
        self.progress.cancel.setEnabled(False)
        self.banner.description.setText(
            "Restoring artwork… You can change folders when this finishes."
        )
        self.open_view("progress")
        cfg = copy(self.ctx.cfg)

        skipped: list[str] = []

        def restore(_: WorkerContext) -> int:
            self.ctx.require_game_closed(Path(cfg.edopro_path))
            return restore_artwork(cfg, skipped=skipped)

        def restored(count: int) -> None:
            self.ctx.cfg.era_art_applied = cfg.era_art_applied
            self.ctx.set_busy(False)
            self.restoring = False
            self.results.undo.setVisible(bool(skipped))
            note = f"{count:,} file {'change' if count == 1 else 'changes'} undone."
            if skipped:
                note += "\nKept edited files. Their undo copies are still available.\n" + "\n".join(
                    skipped
                )
            else:
                note += " Your previous artwork will appear next time you start EDOPro."
            self.summary = Summary(
                "Edited artwork kept" if skipped else "Previous artwork restored",
                note=note,
                warning=bool(skipped),
            )
            self.ctx.state.setValue(self._history_key(), f"Last change   ·   {self.summary.title}")
            self._snapshot(self.ctx.installation)
            self.results.show_result(self.summary)
            self.open_view("results")

        def failed(exc: Exception) -> None:
            self.ctx.set_busy(False)
            if isinstance(exc, GameOpenError):
                self.open_view("blocked")
                self.guard.show_reason(str(exc))
                return
            self.restoring = False
            self.summary = Summary("Could not restore artwork", note=str(exc), warning=True)
            self.results.show_result(self.summary)
            self._snapshot(self.ctx.installation)
            self.open_view("results")

        job = self.ctx.tasks.start(restore)
        job.result.connect(restored)
        job.error.connect(failed)

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()
            self.progress.cancel.setEnabled(False)
            self.progress.phase.setText("Canceling… Finishing the current file.")

    def _reveal_failures(self, expanded: bool) -> None:
        target = self.results.list if expanded else self.results.failures
        target.setFocus()
        area = self.views["results"]
        QTimer.singleShot(0, area, lambda: area.ensureWidgetVisible(target))

    def hidden(self) -> bool:
        if self.ctx.busy:
            if self.restoring:
                self.ctx.notify("Wait for the artwork restore to finish.")
            elif self.job:
                self.ctx.notify("Wait for the current artwork job or cancel it first.")
            else:
                self.ctx.notify("Saving your artwork defaults…")
            return False
        return self._may_leave_editor()

    def _may_leave_editor(self) -> bool:
        if self.state == "settings" or (self.state != "options" and self.origin == "settings"):
            return self.settings.discard(self.ctx)
        return True

    def shown(self) -> None:
        if self.state == "options":
            control = next(
                button
                for button in (
                    self.options.start,
                    self.options.check_again,
                    self.options.connect_game,
                )
                if not button.isHidden()
            )
            if not control.isVisible():
                control = self.options.settings
            control.setFocus()

    def request_close(self) -> bool:
        if self.state == "settings" and self.ctx.busy and self.isVisible():
            return False
        if not self._may_leave_editor():
            return False
        if self.job:
            self.cancel()
        return True

    def escape(self) -> bool:
        if self.job or self.ctx.busy:
            return True
        elif self.state == "settings":
            self._discard_settings()
        elif self.state == "results" and self.results.failures.isChecked():
            self.results.failures.click()
        elif self.state != "options":
            self._back_to_origin()
        else:
            return False
        return True

"""Coordinate the import, undo and artwork actions in the tournament page."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from studio.pages.decks_dialogs import confirm_deck_change
from studio.pages.decks_flow import (
    GameOpen,
    ImportBatch,
    ImportProgress,
    ImportReceipt,
    UndoResult,
    artwork_paths,
    import_decks,
    resume_import,
    undo_import,
)
from studio.pages.decks_handoff import saved_deck_selection
from studio.tasks import Job, Work, WorkerContext
from tournament_decks import DeckError, DeckPublication

if TYPE_CHECKING:
    from studio.pages.decks import DecksPage


class DeckImports:
    def __init__(self, host: DecksPage) -> None:
        self.host = host
        self.job: Job | None = None
        self.receipt: ImportReceipt | None = None
        self.batch: ImportBatch | None = None
        self.undo_pending: tuple[DeckPublication, ...] = ()
        self.removed: set[Path] = set()
        self.undo_notice = ""
        self.retry_action: Callable[[], None] | None = None
        self.blocked_retry: Callable[[], None] | None = None
        self.blocked_from_receipt = False
        self.handoff_available = False

        host.browser.recent.clicked.connect(self.open_receipt)
        host.browser.save.clicked.connect(self.start_import)
        host.import_view.progress.cancel.clicked.connect(self.cancel)
        host.results.review.clicked.connect(self._open_review)
        host.results.undo.clicked.connect(self._confirm_undo)
        host.results.retry.clicked.connect(self._retry)
        host.results.artwork.clicked.connect(self._sync_artwork)
        host.results.open_saved.clicked.connect(self._open_saved)
        host.blocked.block.retry.clicked.connect(self._retry_block)

    def reset(self) -> None:
        self.receipt = self.batch = None
        self.retry_action = None
        self.blocked_retry = None
        self.handoff_available = False
        self._reset_undo()

    def start_import(self) -> None:
        if self.job or self.host.ctx.busy or not self.host.ctx.installation.path:
            return
        batch = self.host.selected_batch()
        if batch is not None:
            self._run_import(batch)

    def _run_import(self, batch: ImportBatch, resume: ImportReceipt | None = None) -> None:
        self.host._discard("artwork")
        self.host.import_view.progress.begin("Preparing deck folder…")
        self.host.import_view.progress.detail.setText("Checking the selected EDOPro installation.")
        self.host._show(self.host.import_view, self.host.import_view.progress.cancel)
        library = self.host.library

        def work(worker: WorkerContext) -> ImportReceipt:
            if resume:
                return resume_import(resume, batch, library, worker.cancel, worker.progress)
            return import_decks(batch, library, worker.cancel, worker.progress)

        job = self._mutate(
            work, lambda receipt: self._import_done(batch, receipt), self._import_error
        )
        job.progress.connect(self._import_progress)

    def _mutate(self, work: Work, done: Callable, failed: Callable[[Exception], None]) -> Job:
        self.host.ctx.set_busy(True)
        self.job = self.host.ctx.tasks.start(work)
        self.job.result.connect(done)
        self.job.error.connect(failed)
        self.job.finished.connect(self._unbusy)
        return self.job

    def _unbusy(self) -> None:
        self.job = None
        self.host.ctx.set_busy(False)

    def _import_progress(self, update: ImportProgress) -> None:
        block = self.host.import_view.progress
        block.phase.setText("Saving to your deck folder")
        block.advance_to(update.done, update.total)
        if self.job is None or self.job.cancel_event.is_set():
            return
        if update.deck:
            block.detail.setText(f"{update.deck.name} · {update.deck.builder}")

    def cancel(self) -> None:
        if self.job:
            self.job.cancel()
            block = self.host.import_view.progress
            block.cancel.setEnabled(False)
            block.detail.setText("Stopping before the next save. Decks already saved will stay.")

    def _import_done(self, batch: ImportBatch, receipt: ImportReceipt) -> None:
        if receipt.game_open and not receipt.paths:
            self._block(receipt.blocked, lambda: self._run_import(batch, receipt))
            return
        self.batch = batch
        self._reset_undo()
        self.receipt = receipt
        self.handoff_available = bool(receipt.paths)
        self.undo_pending = receipt.created
        for entry in receipt.entries:
            if entry.saved:
                self.host.saved.setdefault(entry.deck.id, set()).update(
                    (entry.saved.path, *entry.saved.kept)
                )
        self.host._refresh_saved_rows()
        if not receipt.skipped:
            self.host.completed = batch
        self.host._update_actions()
        if receipt.game_open:
            self._block(
                receipt.blocked, lambda: self._run_import(batch, receipt), from_receipt=True
            )
            return
        self.open_receipt()

    def _import_error(self, exc: Exception) -> None:
        self.host._show(self.host.browser, self.host.browser.save, back=True)
        self.host.ctx.notify(
            f"Couldn’t save the deck batch. {exc}"
            if isinstance(exc, DeckError)
            else "Couldn’t save the deck batch. Try again."
        )

    def _reset_undo(self) -> None:
        self.undo_pending = ()
        self.removed.clear()
        self.undo_notice = ""

    def open_receipt(self) -> None:
        if self.receipt is None:
            return
        receipt = self.receipt
        self.host.results.show_receipt(
            receipt, self.host.ctx.installation.ready, bool(self.undo_pending)
        )
        self.host.results.undo.setEnabled(True)
        self.host.results.artwork.setEnabled(True)
        self.host.results.open_saved.setEnabled(True)
        self.host.results.open_saved.setVisible(bool(self._remaining_paths()))
        self.host.results.artwork.setVisible(
            self.handoff_available
            and self.host.ctx.installation.ready
            and any(path not in self.removed for path in receipt.paths)
        )
        if self.removed:
            self.host.results.mark_removed(self.removed)
        self.retry_action = None
        if receipt.skipped and not receipt.stopped and self.batch is not None and not self.removed:
            batch = self.batch
            self.retry_action = lambda: self._run_import(batch, receipt)
        if self.undo_notice:
            self.host.results.notice.show_message(self.undo_notice)
        self.host.results.retry.setText("Retry skipped decks")
        self.host.results.retry.setVisible(self.retry_action is not None)
        self.host._show(self.host.results, self.host.results.back)

    def _block(self, reason: str, retry: Callable[[], None], *, from_receipt: bool = False) -> None:
        self.blocked_retry = retry
        self.blocked_from_receipt = from_receipt
        view = self.host.blocked
        self.host._show(view, view.block.retry)
        view.block.show_reason(reason)

    def _retry_block(self) -> None:
        if self.blocked_retry and not self.job and not self.host.ctx.busy:
            retry, self.blocked_retry = self.blocked_retry, None
            retry()

    def leave_block(self) -> None:
        self.blocked_retry = None
        if self.blocked_from_receipt:
            self.open_receipt()
        else:
            self.host._show(self.host.browser, self.host.browser.standings, back=True)

    def _remaining_paths(self) -> tuple[Path, ...]:
        return tuple(path for path in self.receipt.paths if path not in self.removed)

    def _open_saved(self) -> None:
        if self.receipt is None or self.job or self.host.ctx.busy:
            return
        selection = saved_deck_selection(self.host.ctx)
        selection.paths = self._remaining_paths()
        if not self.host.ctx.navigate("mydecks"):
            selection.paths = ()

    def _open_review(self) -> None:
        if self.receipt:
            self.host._discard("artwork")
            self.host.results.artwork.setEnabled(True)
            self.host.review.populate(self.receipt)
            self.host._show(self.host.review, self.host.review.back)

    def _retry(self) -> None:
        if self.retry_action and not self.job and not self.host.ctx.busy:
            self.retry_action()

    def _confirm_undo(self) -> None:
        if self.job or self.host.ctx.busy or not self.undo_pending:
            return
        if confirm_deck_change(
            self.host,
            "Remove saved copies",
            "Remove unchanged decks created by this import? Earlier copies and decks you’ve edited will stay.",
        ):
            self._undo()

    def _undo(self) -> None:
        if self.receipt is None or not self.undo_pending:
            return
        self.host._discard("artwork")
        selected, receipt, pending = (
            self.host.ctx.installation.path,
            self.receipt,
            self.undo_pending,
        )
        self.host.results.undo.setEnabled(False)
        self.host.results.retry.hide()
        self.host.results.artwork.setEnabled(False)
        self.host.results.open_saved.setEnabled(False)
        self.host.results.notice.show_message(
            "Removing unchanged copies. You can change folders when it’s done."
        )
        self._mutate(
            lambda _: undo_import(selected, receipt, pending), self._undo_done, self._undo_error
        )

    def _undo_done(self, outcome: UndoResult) -> None:
        self.removed.update(outcome.removed)
        self.undo_pending = outcome.pending
        for deck_id, paths in tuple(self.host.saved.items()):
            paths.difference_update(outcome.removed)
            if not paths:
                del self.host.saved[deck_id]
        self.host._refresh_saved_rows()
        if outcome.removed:
            self.host.completed = None
            self.host._update_actions()
        self.undo_notice = f"Removed {len(self.removed)} {'deck' if len(self.removed) == 1 else 'decks'} from this import."
        if outcome.kept:
            self.undo_notice += f" Kept {len(outcome.kept)} changed or moved copies."
        if outcome.blocked:
            self._block(outcome.blocked, self._undo, from_receipt=True)
            return
        elif outcome.pending:
            self.undo_notice += " Some copies couldn’t be removed. Choose Retry to try again."
        self.open_receipt()
        if outcome.pending:
            self.retry_action = self._undo
            self.host.results.retry.setText("Retry undo")
            self.host.results.retry.show()
            self.host.results.retry.setFocus()

    def _undo_error(self, error: Exception) -> None:
        self.open_receipt()
        self.host.results.notice.show_message(
            str(error) if isinstance(error, DeckError) else "Couldn’t undo this import. Try again."
        )

    def _sync_artwork(self) -> None:
        if self.receipt is None or self.job or self.host.ctx.busy:
            return
        selected, receipt, removed = (
            self.host.ctx.installation.path,
            self.receipt,
            frozenset(self.removed),
        )
        self.host.results.artwork.setEnabled(False)
        self.host.results.retry.hide()
        self.host.results.notice.show_message("Checking saved decks…")
        self.host._read(
            "artwork",
            lambda _: artwork_paths(selected, receipt, removed),
            lambda paths: self._handoff(receipt.game, paths),
            self._artwork_error,
        )

    def _artwork_error(self, error: Exception) -> None:
        self.host.results.artwork.setEnabled(True)
        self.host.results.notice.show_message(
            str(error)
            if isinstance(error, DeckError)
            else "Couldn’t open the saved decks. Try again."
        )
        if isinstance(error, GameOpen):
            self._block(error.args[0], self._retry_artwork, from_receipt=True)
        elif isinstance(error, DeckError):
            self.handoff_available = False
            self.host.results.artwork.hide()

    def _retry_artwork(self) -> None:
        self.open_receipt()
        self._sync_artwork()

    def _handoff(self, game: Path, paths: tuple[Path, ...]) -> None:
        self.host.results.artwork.setEnabled(True)
        if self.host.ctx.sync_deck_files(game, paths):
            self.host.results.notice.show_message("")
        else:
            self.host.results.notice.show_message("HD Cards is busy. Try again in a moment.")

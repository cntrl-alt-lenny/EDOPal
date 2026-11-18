"""Personal deck library for the selected EDOPro installation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from artwork_flow import valid_decks
from decks import DeckIndexer, Library, Preview, SavedDeck, Sections, encode_ydke, preview
from decks.duplicates import DuplicateIndex
from decks.history import DeckStore, Ledger, Track
from studio import theme as t
from studio.context import AppContext, GameOpenError, Installation
from studio.pages.base import Page
from studio.pages.decks_dialogs import confirm_deck_change
from studio.pages.decks_handoff import HD_ARTWORK, saved_deck_selection
from studio.pages.decks_layout import DeckPageScroll
from studio.pages.mydecks_checks import Comparison, FormatCheck
from studio.pages.mydecks_copies import CopyReview
from studio.pages.mydecks_duplicates import DuplicatesScreen
from studio.pages.mydecks_files import (
    Receipt,
    publish,
    remove_copy,
    require_closed,
    reveal_file,
)
from studio.pages.mydecks_history import HistoryScreen, MissingDecks
from studio.pages.mydecks_library import Collection
from studio.pages.mydecks_logic import file_problem, quantity, scan_collection
from studio.pages.mydecks_odds import OddsScreen
from studio.pages.mydecks_widgets import Contents, Screen, button
from studio.tasks import Job, WorkerContext
from studio.widgets import ElidedLabel, GameClosedBlock, Panel, SectionHeader, label, margins
from tournament_decks import DeckError


class MyDecksPage(Page):
    id, title, icon = "mydecks", "My decks", "deck"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.history: list[Screen] = []
        self.epoch = 0
        self.active = self.writing = False
        self.index: DeckIndexer | None = None
        self.scan_job: Job | None = None
        self.lib: Library | None = None
        self.store = DeckStore(Path(context.cfg.config_path).parent / "deck-tools.sqlite3")
        self.ledger = Ledger({}, ())
        self.duplicates = DuplicateIndex((), {}, {})
        self.receipts: dict[Path, Receipt] = {}
        self.pending_selection: tuple[Path, ...] = ()
        self.retry_action: Callable | None = None
        self.blocked_action: Callable[[], None] | None = None
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(0)
        self.status = QWidget()
        status_layout = QHBoxLayout(self.status)
        status_layout.setContentsMargins(t.XXL, t.SM, t.XXL, 0)
        status_panel = Panel(t.MD)
        status_panel.setMaximumWidth(t.CONTENT_MAX - t.XXL * 2)
        status_layout.addWidget(status_panel)
        status_row = QHBoxLayout()
        self.message = label("", "body", True)
        status_row.addWidget(self.message, 1)
        self.retry = button("Retry", self._retry)
        status_row.addWidget(self.retry)
        status_panel.body.addLayout(status_row)
        self.status.hide()
        body.addWidget(self.status)
        self.blocked = QWidget()
        recovery = QVBoxLayout(self.blocked)
        recovery.setContentsMargins(t.XXL, t.XL, t.XXL, t.XL)
        self.game_block = GameClosedBlock()
        self.game_block.setMaximumWidth(t.CONTENT_MAX - 2 * t.XXL)
        self.game_block.retry.clicked.connect(self._retry_block)
        recovery.addWidget(self.game_block)
        recovery.addStretch()
        self.blocked.hide()
        body.addWidget(self.blocked, 1)
        self.scroll = DeckPageScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.slide = QPropertyAnimation(self)
        self.slide.setPropertyName(b"pos")
        self.slide.setDuration(t.MOTION_MS)
        self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.scroll, 1)

    @property
    def view(self) -> Screen:
        return self.history[-1]

    def shown(self) -> None:
        self.active = True
        selection = saved_deck_selection(self.ctx)
        if selection.paths:
            self.pending_selection, selection.paths = selection.paths, ()
            self.refresh()
        elif len(self.history) < 2 and self.blocked_action is None:
            self.refresh()

    def hidden(self) -> bool:
        if self.writing or self._saving_odds():
            return False
        self._cancel_scan()
        self.active = False
        self.epoch += 1
        return True

    def installation_changed(self, installation: Installation) -> None:
        self._cancel_scan()
        self._clear_block()
        self.epoch += 1
        self.pending_selection = ()
        self.index = DeckIndexer(Path(installation.path)) if installation.path else None
        self.lib = None
        self.ledger = Ledger({}, ())
        self.duplicates = DuplicateIndex((), {}, {})
        if self.active:
            self.refresh()
        else:
            self.slide.stop()
            self.scroll.takeWidget()
            for screen in self.history:
                self._forget(screen)
            self.history.clear()

    def _forget(self, screen: Screen) -> None:
        if isinstance(
            screen,
            (FormatCheck, Comparison, CopyReview, HistoryScreen, OddsScreen, DuplicatesScreen),
        ):
            screen.leave()
        screen.hide()
        screen.deleteLater()

    def _push(self, screen: Screen, *, root: bool = False) -> None:
        self._clear_block()
        self.slide.stop()
        old = self.scroll.takeWidget()
        if old:
            old.hide()
            old.setParent(self)
        if root:
            for previous in self.history:
                self._forget(previous)
            self.history.clear()
        if screen.back is not None and self.history:
            screen.back.setText(self.view.return_name)
        self.history.append(screen)
        self._display(screen)
        self.scroll.verticalScrollBar().setValue(0)
        self.status.hide()
        if screen.initial:
            screen.initial.setFocus()
        if not root:
            self._slide_in(t.XL)

    def _slide_in(self, offset: int) -> None:
        end = self.view.pos()
        self.slide.setTargetObject(self.view)
        self.slide.setStartValue(end + QPoint(offset, 0))
        self.slide.setEndValue(end)
        self.slide.start()

    def escape(self) -> bool:
        if self.writing or self._saving_odds():
            return True
        if self.blocked_action is not None:
            self._clear_block()
            if self.view.initial:
                self.view.initial.setFocus()
            return True
        if len(self.history) < 2:
            return False
        self.slide.stop()
        self.scroll.takeWidget()
        self._forget(self.history.pop())
        self.status.hide()
        self._display(self.view)
        if self.view.initial:
            self.view.initial.setFocus()
        self._slide_in(-t.XL)
        return True

    def _display(self, screen: Screen) -> None:
        fixed = isinstance(
            screen,
            (
                Collection,
                HistoryScreen,
                OddsScreen,
                DuplicatesScreen,
                MissingDecks,
                CopyReview,
                FormatCheck,
                Comparison,
            ),
        ) or hasattr(screen, "contents")
        self.scroll.setWidgetResizable(not fixed)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if fixed else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll.setWidget(screen)

    def _state(
        self, title: str, description: str, action: str = "", callback: Callable | None = None
    ) -> None:
        screen = Screen()
        screen.body.addWidget(SectionHeader(title, description))
        if action and callback:
            screen.initial = button(action, callback, "primary")
            screen.body.addWidget(screen.initial, 0, Qt.AlignmentFlag.AlignLeft)
        screen.body.addStretch()
        self._push(screen, root=True)

    def refresh(self, *, after: Callable[[], None] | None = None) -> None:
        if self.writing:
            return
        self.epoch += 1
        self._cancel_scan()
        epoch = self.epoch
        install = self.ctx.installation
        if not install.path:
            self._state(
                "Choose your game",
                "Select an EDOPro installation to open its deck library.",
                "Choose folder",
                self.ctx.installations_requested.emit,
            )
            return
        if not install.ready and not self.pending_selection:
            self._state(
                "Waiting for cards",
                "Open EDOPro once to update its card data, then check again.",
                "Check again",
                self._inspect,
            )
            return
        index = self.index
        if index is None:
            self.index = index = DeckIndexer(Path(install.path))
        self._state("Reading your decks", "Finding saved decks and looking up their cards.")
        store = self.store
        state_available = True

        def work(worker: WorkerContext) -> Library:
            lib, ledger, duplicates, available = scan_collection(index, store, worker.cancel)
            # Health check consumes the Library when it opens a specific deck or format.
            worker.progress((ledger, duplicates, available))
            return lib

        def tools_ready(result: tuple[Ledger, DuplicateIndex, bool]) -> None:
            nonlocal state_available
            if self.epoch == epoch:
                self.ledger, self.duplicates, state_available = result

        self.scan_job = job = self.ctx.tasks.start(work)

        def ready(lib: Library) -> None:
            if self.epoch != epoch:
                return
            self.scan_job = None
            self.lib = lib
            view = Collection(
                lib,
                self.detail,
                self.check,
                self.compare,
                self.import_code,
                self.refresh,
                self.duplicates,
                self.show_duplicates,
                len(self.ledger.missing),
                self.show_missing,
            )
            self._push(view, root=True)
            current_paths = {d.path for d in lib.decks}
            if not lib.issues and (
                latest := next(
                    (r for r in reversed(self.receipts.values()) if r.path in current_paths),
                    None,
                )
            ):
                self._receipt_bar(view, latest)
            if self.pending_selection:
                found = view.select_saved(self.pending_selection)
                if found < len(self.pending_selection):
                    self._notice("Some saved decks are missing from this deck folder.")
                self.pending_selection = ()
            if not state_available:
                self._notice("Deck history and card groups are unavailable. Refresh to try again.")
            if after:
                after()

        job.progress.connect(tools_ready)
        job.result.connect(ready)
        job.error.connect(
            lambda exc: (
                self._state("Library unavailable", str(exc), "Retry", self.refresh)
                if epoch == self.epoch
                else None
            )
        )

    def _cancel_scan(self) -> None:
        if self.scan_job is not None:
            self.scan_job.cancel()
            self.scan_job = None

    def _saving_odds(self) -> bool:
        return bool(self.history and isinstance(self.view, OddsScreen) and self.view.saving)

    def _inspect(self) -> None:
        path, epoch = self.ctx.installation.path, self.epoch
        self.view.initial.setEnabled(False)
        job = self.ctx.tasks.start(lambda _: Installation.inspect(path))
        job.result.connect(
            lambda selection: self.ctx.set_installation(selection) if epoch == self.epoch else None
        )
        job.error.connect(
            lambda exc: (
                self._state("Folder unavailable", str(exc), "Retry", self.refresh)
                if epoch == self.epoch
                else None
            )
        )

    def check(self, deck: SavedDeck | None = None, selected: int = 0) -> None:
        def scanned() -> None:
            current = next((d for d in self.lib.decks if deck and d.path == deck.path), None)
            self.check(current)

        self._push(
            FormatCheck(
                self.lib,
                self.ctx.tasks,
                self.escape,
                self.check,
                deck,
                selected,
                lambda: self.refresh(after=scanned),
            )
        )

    def compare(self) -> None:
        self._push(Comparison(self.lib, self.ctx.tasks, self.escape))

    def show_history(self, track: Track) -> None:
        self._push(
            HistoryScreen(
                track, self.store, self.lib.catalog, self.ctx.tasks, self.escape, self._save
            )
        )

    def show_missing(self) -> None:
        self._push(MissingDecks(self.ledger.missing, self.lib.game, self.escape, self.show_history))

    def show_odds(self, track: Track, deck: Preview) -> None:
        self._push(OddsScreen(track, deck, self.store, self.ctx.tasks, self.escape, self._confirm))

    def show_duplicates(self, deck: SavedDeck | None = None) -> None:
        self._push(
            DuplicatesScreen(
                self.duplicates, self.ctx.tasks, self.escape, self.detail, self._reveal, deck
            )
        )

    def import_code(self, deck: SavedDeck | None = None) -> None:
        screen = CopyReview(self.lib, self.ctx.tasks, self.escape, self._save, deck)
        if deck is None:
            screen.code.textChanged.connect(self._clear_write_notice)
        screen.name.textChanged.connect(self._clear_write_notice)
        self._push(screen)

    def detail(self, deck: SavedDeck) -> None:
        screen = Screen(deck.name, self.escape)
        screen.return_name = "Deck details"
        path = ElidedLabel()
        path.set_value(str(deck.path), deck.path.relative_to(self.lib.game).as_posix())
        screen.body.addWidget(path)
        panel = Panel(t.LG)
        if deck.preview and deck.preview.unresolved:
            n = len(deck.preview.unresolved)
            panel.body.addWidget(
                label(
                    f"The game is missing information for {quantity(n, 'card')}. "
                    "Review possible replacements in Repair a copy.",
                    "body",
                    True,
                )
            )
        buttons = QHBoxLayout()
        buttons.addWidget(button("Show file", lambda: self._reveal(deck.path), symbol="folder"))
        if deck.preview:
            buttons.addWidget(
                button("Copy code", lambda: self._copy_code(deck.preview), symbol="link")
            )
            if deck.preview.unresolved:
                buttons.addWidget(button("Repair a copy", lambda: self.import_code(deck)))
        buttons.addStretch()
        buttons.addWidget(button("Check format", lambda: self.check(deck), "secondary", "check"))
        panel.body.addLayout(buttons)
        track = self.ledger.active.get(deck.path)
        similar = deck.path in self.duplicates.by_path
        if track is not None or similar:
            tools = QHBoxLayout()
            if track is not None:
                tools.addWidget(
                    button("History", lambda: self.show_history(track), symbol="replays")
                )
                if deck.preview:
                    tools.addWidget(
                        button("Opening odds", lambda: self.show_odds(track, deck.preview))
                    )
            if similar:
                tools.addWidget(
                    button("Similar decks", lambda: self.show_duplicates(deck), symbol="deck")
                )
            tools.addStretch()
            panel.body.addLayout(tools)
        screen.body.addWidget(panel)
        if deck.preview:
            screen.contents = Contents(self.ctx.tasks, self.lib.game, deck.preview)
            screen.body.addWidget(screen.contents, 1)
        else:
            screen.body.addWidget(
                label(
                    f"The game’s saved deck could not be read. {file_problem(deck.error)}",
                    "body",
                    True,
                )
            )
            screen.body.addStretch()
        if deck.preview:
            footer = QHBoxLayout()
            footer.addWidget(
                label(
                    "To edit cards, open Deck Edit in EDOPro and choose this deck.", "muted", True
                ),
                1,
            )
            artwork = button(HD_ARTWORK, lambda: self._art(deck), "primary", "download")
            artwork.setEnabled(self.ctx.installation.ready)
            if not artwork.isEnabled():
                artwork.setToolTip("Open EDOPro to download its cards first.")
            footer.addWidget(artwork)
            screen.body.addLayout(footer)
        self._push(screen)
        if receipt := self.receipts.get(deck.path):
            self._receipt_bar(screen, receipt)

    def _copy_code(self, preview: Preview) -> None:
        try:
            code = encode_ydke(preview.sections)
        except ValueError as exc:
            self._notice(str(exc))
            return
        QApplication.clipboard().setText(code)
        self._notice("Sharing code copied. Paste it to share all three sections.")

    def _notice(self, message: str, retry: Callable | None = None) -> None:
        self.retry_action = retry
        self.retry.setVisible(retry is not None)
        self.message.setText(message)
        self.status.show()
        if retry is not None and self.active:
            self.retry.setFocus()

    def _retry(self) -> None:
        if self.retry_action and not self.writing:
            self.retry_action()

    def _clear_block(self) -> None:
        self.blocked_action = None
        self.blocked.hide()
        self.scroll.show()

    def _clear_write_notice(self) -> None:
        self.retry_action = None
        self.status.hide()
        self._clear_block()

    def _retry_block(self) -> None:
        if self.blocked_action is not None and not self.writing:
            action = self.blocked_action
            self._clear_block()
            action()

    def _confirm(self, action: str, message: str) -> bool:
        return confirm_deck_change(self, action, message)

    def _reveal(self, path: Path) -> None:
        epoch = self.epoch
        job = self.ctx.tasks.start(lambda _: reveal_file(path))
        job.error.connect(lambda exc: self._notice(str(exc)) if epoch == self.epoch else None)

    def _art(self, deck: SavedDeck) -> None:
        game, selected = self.lib.game, self.ctx.installation.path
        paths = (deck.path,)

        def check() -> None:
            if not valid_decks(selected, game, paths):
                raise DeckError("This deck has moved. Refresh the library and try again.")
            require_closed(game)

        def start(_: None) -> None:
            if not self.ctx.sync_deck_files(game, paths):
                self._notice("Artwork could not start. Refresh the library and try again.")

        self._write("Checking saved decks…", check, start)

    def _save(self, name: str, contents: Preview | Sections) -> None:
        game = self.lib.game
        catalog = self.lib.catalog

        def saved(receipt: Receipt) -> None:
            self.receipts[receipt.path] = receipt
            self.pending_selection = (receipt.path,)
            self.refresh()

        def work() -> Receipt:
            reviewed = preview(contents, catalog) if isinstance(contents, Sections) else contents
            return publish(game, name, reviewed)

        self._write("Saving a new deck copy…", work, saved)

    def _receipt_bar(self, screen: Screen, receipt: Receipt) -> None:
        row = QHBoxLayout()
        msg = ElidedLabel()
        msg.set_value(f"Saved {receipt.path.name} · Available next time you start EDOPro.")
        row.addWidget(msg, 1)
        if receipt.stamp:
            row.addWidget(button("Remove saved copy", lambda: self._undo(receipt)))
        else:
            msg.set_value(
                f"Saved {receipt.path.name}. Use Show file to locate the copy for removal."
            )
            row.addWidget(button("Show file", lambda: self._reveal(receipt.path)))
        screen.body.insertLayout(1, row)

    def _undo(self, receipt: Receipt) -> None:
        if not self._confirm(
            "Remove saved copy", f"Remove only {receipt.path.name} from your deck library?"
        ):
            return
        game = self.lib.game

        def removed(_: None) -> None:
            self.receipts.pop(receipt.path, None)
            self.refresh()

        self._write("Removing the saved copy…", lambda: remove_copy(game, receipt), removed)

    def _write(self, title: str, work: Callable, done: Callable) -> None:
        if self.writing or self.ctx.busy:
            return
        self.writing = True
        self.ctx.set_busy(True)
        self.scroll.setEnabled(False)
        self._notice(title + " Stay on this page until it finishes.")
        job = self.ctx.tasks.start(lambda _: work())

        def release() -> None:
            self.writing = False
            self.ctx.set_busy(False)
            self.scroll.setEnabled(True)

        def success(result: Any) -> None:
            release()
            self.status.hide()
            done(result)

        def failed(error: BaseException) -> None:
            release()

            def retry() -> None:
                self._write(title, work, done)

            if isinstance(error, GameOpenError):
                self.status.hide()
                self.scroll.hide()
                self.blocked_action = retry
                self.blocked.show()
                self.game_block.show_reason(str(error))
            else:
                self._notice(str(error), retry)

        job.result.connect(success)
        job.error.connect(failed)

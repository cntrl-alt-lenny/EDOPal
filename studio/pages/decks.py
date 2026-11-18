"""The tournament browser. Network and installation work run through AppContext."""

from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QPoint, QPropertyAnimation, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QApplication, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from goat_versions import installed_whitelist
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.decks_browser import Browser, ListMessage
from studio.pages.decks_flow import (
    EventPage,
    ImportBatch,
    StandingsSelection,
    format_choices,
    is_goat,
)
from studio.pages.decks_import import DeckImports
from studio.pages.decks_layout import DeckPageScroll
from studio.pages.decks_preview_data import PreviewSection, prepare_preview
from studio.pages.decks_views import (
    BlockedSave,
    GoatReviewView,
    ImportView,
    PreviewView,
    ReceiptView,
)
from studio.tasks import Job, Work
from studio.widgets import margins
from tournament_decks import SITE, Deck, DeckError, Event, FormatLibrary


class DecksPage(Page):
    id, title, icon = "decks", "Tournament decks", "decks"

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.library = FormatLibrary()
        self.page = EventPage()
        self.events: tuple[Event, ...] = ()
        self.selection = StandingsSelection()
        self.has_loaded = False
        self.formats_loaded = False
        self.goat_available = False
        self.preview_target: tuple[Event, Deck] | None = None
        self.preview_cache: dict[int, tuple[PreviewSection, ...]] = {}
        self.unpublished: set[int] = set()
        self.completed: ImportBatch | None = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(t.MOTION_MS)
        self.preview_timer.timeout.connect(self._load_preview)
        self.saved: dict[int, set[Path]] = {}
        self.reads: dict[str, Job] = {}

        body = QVBoxLayout(self)
        margins(body, 0)
        self.shell = QStackedWidget()
        body.addWidget(self.shell)
        self.scrolls: dict[QWidget, QScrollArea] = {}
        self.preview = PreviewView(context.tasks)
        self.browser = Browser(self.preview)
        self.import_view = ImportView()
        self.results = ReceiptView()
        self.review = GoatReviewView()
        self.blocked = BlockedSave()
        for view in (self.browser, self.import_view, self.results, self.review, self.blocked):
            view.setMaximumWidth(t.CONTENT_MAX)
            scroll = DeckPageScroll()
            fixed = view in (self.browser, self.results)
            scroll.setWidgetResizable(not fixed)
            if fixed:
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            scroll.setWidget(view)
            self.shell.addWidget(scroll)
            self.scrolls[view] = scroll

        self.slide = QPropertyAnimation(self)
        self.slide.setPropertyName(b"pos")
        self.slide.setDuration(t.MOTION_MS)
        self.slide.setEndValue(QPoint())

        self.imports = DeckImports(self)
        b = self.browser
        b.submit.clicked.connect(self.search)
        b.search.returnPressed.connect(self.search)
        b.format.currentTextChanged.connect(self.search)
        b.choose.clicked.connect(context.installations_requested.emit)
        b.credit.clicked.connect(lambda: self._open_url(f"{SITE}/events"))
        b.event_message.retry.clicked.connect(self._event_recovery)
        b.standings_message.retry.clicked.connect(self._standings_recovery)
        b.previous.clicked.connect(lambda: self.load_events(self.page.previous()))
        b.next.clicked.connect(lambda: self.load_events(self.page.following(self.events)))
        b.events.currentItemChanged.connect(self._event_selected)
        b.events.itemActivated.connect(b.show_standings)
        b.events.itemClicked.connect(b.show_standings)
        b.standings.itemSelectionChanged.connect(self._decks_selected)
        b.standings.currentItemChanged.connect(self._standing_focused)
        b.standings.itemActivated.connect(self._focus_preview)
        b.open_preview.clicked.connect(self._focus_preview)
        b.goat.toggled.connect(self._update_actions)
        b.event_link.clicked.connect(self._visit_event)
        b.select_all.clicked.connect(b.standings.selectAll)
        self.preview.message.retry.clicked.connect(self._load_preview)
        self.preview.source.clicked.connect(self._visit_deck)
        self.results.back.clicked.connect(self.escape)
        self.review.back.clicked.connect(self.escape)
        self._installation_controls(context.installation)

    @property
    def view(self) -> QWidget:
        return self.shell.currentWidget().widget()

    def _show(self, view: QWidget, focus: QWidget, *, back: bool = False) -> None:
        self.slide.stop()
        self.shell.currentWidget().move(0, 0)
        scroll = self.scrolls[view]
        if scroll is not self.shell.currentWidget():
            self.shell.setCurrentWidget(scroll)
            if not back:
                scroll.verticalScrollBar().setValue(0)
            if self.isVisible():
                self.slide.setTargetObject(scroll)
                self.slide.setStartValue(QPoint(-t.XL if back else t.XL, 0))
                self.slide.start()
        focus.setFocus()

    def shown(self) -> None:
        if self.ctx.installation.path and not self.has_loaded:
            self.load_events(self.page)
        if self.view is self.browser:
            (self.browser.search if self.ctx.installation.path else self.browser.choose).setFocus()
        elif self.view in (self.results, self.review):
            self.view.back.setFocus()
        elif self.view is self.blocked:
            self.blocked.block.retry.setFocus()

    def hidden(self) -> bool:
        if self.imports.job is not None:
            return False
        self._discard("artwork")
        self.results.artwork.setEnabled(True)
        return True

    def request_close(self) -> bool:
        self.preview_timer.stop()
        if self.view is self.import_view:
            self.imports.cancel()
        return True

    def escape(self) -> bool:
        if self.imports.job is not None:
            if self.view is self.import_view:
                self.imports.cancel()
            return True
        if self.view is self.review:
            self._show(self.results, self.results.review, back=True)
            return True
        if self.view is self.results:
            self._discard("artwork")
            self.results.artwork.setEnabled(True)
            self.browser.show_standings(focus=False)
            self._show(self.browser, self.browser.standings, back=True)
            return True
        if self.view is self.blocked:
            self.imports.leave_block()
            return True
        if self.browser.preview_open:
            self.browser.show_standings()
            return True
        focused = QApplication.focusWidget()
        if focused is not None and self.preview.isAncestorOf(focused):
            self.browser.show_standings()
            self.scrolls[self.browser].ensureWidgetVisible(self.browser.standings)
            return True
        if self.browser.events_open and self.selection.event and self.browser.layout_mode != "wide":
            self.browser.show_standings()
            return True
        return False

    def installation_changed(self, installation: Installation) -> None:
        self._discard(*self.reads)
        self.preview_cache.clear()
        self.unpublished.clear()
        self.completed = None
        self.has_loaded = False
        self.imports.reset()
        self.saved.clear()
        self.browser.goat.setChecked(False)
        self._installation_controls(installation)
        self._show(self.browser, self.browser.search, back=True)
        self._clear_events()
        if self.isVisible():
            self.shown()

    def _installation_controls(self, installation: Installation) -> None:
        b = self.browser
        b.has_installation = bool(installation.path)
        for control in (b.format, b.search, b.submit):
            control.setEnabled(bool(installation.path))
        b.results.setVisible(bool(installation.path))
        b.unavailable.setVisible(not installation.path)
        b.readiness.setVisible(bool(installation.path) and not installation.ready)
        if installation.path:
            folder = Path(installation.path) / "deck"
            b.destination.set_value(str(folder), f"Save to {folder.parent.name}")
        b._show_archive_panel()
        self._update_actions()

    def _discard(self, *keys: str) -> None:
        for key in keys:
            if job := self.reads.pop(key, None):
                job.cancel()

    def _read(
        self, key: str, work: Work, done: Callable, failed: Callable[[Exception], None]
    ) -> None:
        self._discard(key)
        job = self.ctx.tasks.start(lambda worker: None if worker.cancel.is_set() else work(worker))
        self.reads[key] = job

        def deliver(callback: Callable, value: object) -> None:
            if self.reads.get(key) is job:
                self.reads.pop(key)
                callback(value)

        job.result.connect(lambda value: deliver(done, value))
        job.error.connect(lambda error: deliver(failed, error))

    def search(self) -> None:
        self.load_events(
            EventPage.search(self.browser.format.currentText(), self.browser.search.text())
        )

    def _clear_events(self) -> None:
        self.events = ()
        self.browser.set_events(())
        self.load_standings(None)

    def load_events(self, page: EventPage | None) -> None:
        if page is None or not self.ctx.installation.path or self.imports.job:
            return
        self.page = page
        self.has_loaded = True
        self._clear_events()
        self.preview.reset("Finding tournaments…", "Looking up published decklists.")
        self.browser.event_context.setText(f"{page.format or 'All formats'} · Latest first")
        self.browser.page_number.setText(f"Page {page.number}")
        self.browser.event_message.set_copy(
            "Finding tournaments…", "Looking up Format Library.", False
        )
        self._read(
            "events",
            lambda _: self.library.events(page.format, page.query, page.number),
            self._events_loaded,
            self._events_failed,
        )
        if not self.formats_loaded and "formats" not in self.reads:
            self._read(
                "formats", lambda _: self.library.formats(), self._formats_loaded, lambda _: None
            )

    def _formats_loaded(self, choices: tuple[str, ...]) -> None:
        self.formats_loaded = True
        self.browser.set_formats(format_choices(choices))

    def _events_loaded(self, events: tuple[Event, ...]) -> None:
        self.events = events
        self.browser.set_events(events)
        if events:
            self.load_standings(events[0])
            self.browser.show_standings(focus=False)
        else:
            if self.page.previous():
                hint, action = "There are no more events for this search.", "Previous page"
            elif self.page.query:
                hint, action = "Try another event name or code.", "Clear search"
            elif self.page.format:
                hint, action = "Format Library has no events in this format.", "Show all formats"
            else:
                hint, action = "Format Library has no public events available.", "Retry"
            self.browser.event_message.set_copy("No tournaments found", hint, True, action)
        self._update_actions()

    def _event_selected(self) -> None:
        index = self.browser.events.currentIndex().row()
        if 0 <= index < len(self.events) and self.events[index] != self.selection.event:
            self.load_standings(self.events[index])

    def load_standings(self, event: Event | None) -> None:
        self._discard("standings", "goat")
        self._cancel_preview()
        self.preview_target = None
        self.selection.open(event)
        self.goat_available = False
        b = self.browser
        b.has_event = event is not None
        if event is None or not is_goat(event):
            b.goat.setChecked(False)
        b.set_standings((), self.saved)
        if event:
            self.preview.reset("Loading standings…", "Looking up published decklists.")
        else:
            self.preview.reset("Choose a tournament", "Published decklists will appear here.")
            b.browse_events()
        meta = (
            f"{event.format} · {event.players:,} players"
            if event and event.players
            else (event.format if event else "Public decklists")
        )
        b.event_meta.setText(meta)
        b.standings_message.set_copy(
            "Loading standings…" if event else "Choose a tournament",
            "Looking up published decks." if event else "Public decklists will appear here.",
            False,
        )
        self._update_actions()
        if event is not None:
            game = Path(self.ctx.installation.path)
            self._read(
                "standings",
                lambda _: self.library.standings(event),
                self._standings_loaded,
                self._standings_failed,
            )
            if is_goat(event) and self.ctx.installation.ready:
                self._read(
                    "goat",
                    lambda _: bool(installed_whitelist(game)),
                    self._goat_loaded,
                    lambda _: self._goat_loaded(False),
                )

    def _goat_loaded(self, available: bool) -> None:
        self.goat_available = available
        if not available:
            self.browser.goat.setChecked(False)
        self._update_actions()

    def _standings_loaded(self, decks: tuple[Deck, ...]) -> None:
        self.selection.populate(decks)
        self.browser.set_standings(decks, self.saved)
        if decks:
            self._standing_focused()
        else:
            self.browser.standings_message.set_copy(
                "No public decklists",
                "This event hasn’t published any decklists.",
                True,
                "Browse events",
            )
        self._update_actions()

    @staticmethod
    def _error_copy(error: Exception, title: str) -> tuple[str, str]:
        cause = error.__cause__
        if isinstance(cause, (URLError, OSError)) and not isinstance(cause, HTTPError):
            title = "Format Library offline"
        detail = str(error) if isinstance(error, DeckError) else "Something went wrong. Try again."
        return title, detail

    def _archive_error(self, message: ListMessage, error: Exception, title: str) -> None:
        title, detail = self._error_copy(error, title)
        message.set_copy(title, detail, True)

    def _event_recovery(self) -> None:
        action = self.browser.event_message.retry.text()
        if action == "Clear search":
            self.browser.search.clear()
            self.search()
        elif action == "Show all formats":
            self.browser.format.setCurrentText("All formats")
        elif action == "Previous page":
            self.load_events(self.page.previous())
        else:
            self.load_events(self.page)

    def _standings_recovery(self) -> None:
        if self.browser.standings_message.retry.text() == "Browse events":
            self.browser.browse_events()
            self.browser.events.setFocus()
        else:
            self.load_standings(self.selection.event)

    def _events_failed(self, error: Exception) -> None:
        self._archive_error(self.browser.event_message, error, "Search unavailable")

    def _standings_failed(self, error: Exception) -> None:
        self._archive_error(self.browser.standings_message, error, "Standings unavailable")

    def _decks_selected(self) -> None:
        self.selection.select(
            {
                item.data(0, Qt.ItemDataRole.UserRole)
                for item in self.browser.standings.selectedItems()
            }
        )
        self._update_actions()

    def _update_actions(self) -> None:
        b = self.browser
        count = len(self.selection.selected)
        batch = self.selected_batch()
        available = len(batch.decks) if batch else 0
        if self.completed != batch:
            self.completed = None
        completed = batch is not None and self.completed == batch
        b.previous.setEnabled(self.page.previous() is not None)
        b.next.setEnabled(self.page.following(self.events) is not None)
        b.event_link.setEnabled(self.selection.event is not None)
        b.select_all.setEnabled(self.selection.can_select_all)
        b.open_standings.setEnabled(self.selection.event is not None)
        b.save.setEnabled(bool(available and not completed))
        b.save.setText(
            "Saved" if completed else f"Add {available} decks" if available > 1 else "Add to EDOPro"
        )
        suffix = f" · {count - available} unpublished" if count > available else ""
        b.selection_count.setText(f"{count} selected{suffix}")
        b.goat.setVisible(self.goat_available)
        b.recent.setVisible(self.imports.receipt is not None)

    def selected_batch(self) -> ImportBatch | None:
        if not self.ctx.installation.path:
            return None
        batch = self.selection.capture(
            Path(self.ctx.installation.path), self.goat_available and self.browser.goat.isChecked()
        )
        if batch is None:
            return None
        decks = tuple(deck for deck in batch.decks if deck.id not in self.unpublished)
        return ImportBatch(batch.game, batch.event, decks, batch.historical) if decks else None

    def _focus_preview(self) -> None:
        if self.browser.layout_mode == "stacked":
            self.browser.show_preview()
            return
        target = (
            self.preview.contents.cards
            if self.preview.contents.isVisible()
            else self.preview.source
        )
        if target.isVisible():
            self.scrolls[self.browser].ensureWidgetVisible(self.preview, t.SM, t.SM)
            target.setFocus()

    def _cancel_preview(self) -> None:
        self.preview_timer.stop()
        self._discard("preview")

    def _standing_focused(self) -> None:
        event = self.selection.event
        row = self.browser.standings.currentIndex().row()
        if event is None or not 0 <= row < len(self.selection.decks):
            self._cancel_preview()
            self.preview_target = None
            self.preview.reset(
                "Choose a standing", "Select a player to see their published decklist."
            )
            return
        deck = self.selection.decks[row]
        if self.preview_target == (event, deck):
            return
        self._cancel_preview()
        self.preview_target = event, deck
        self.preview.start(event, deck)
        if deck.id in self.unpublished:
            self._show_unpublished()
        elif deck.id in self.preview_cache:
            self.preview.loaded(self.preview_cache[deck.id])
        else:
            self.preview.show_message("Loading decklist…", "Reading the published cards.")
            self.preview_timer.start()

    def _load_preview(self) -> None:
        if self.preview_target is None:
            return
        _, deck = self.preview_target
        game = Path(self.ctx.installation.path)
        library = self.library
        self.preview.show_message("Loading decklist…", "Reading the published cards.")

        def ready(sections: tuple[PreviewSection, ...]) -> None:
            self.preview_cache[deck.id] = sections
            self.preview.loaded(sections)

        self._read(
            "preview",
            lambda worker: prepare_preview(game, library.preview(deck), worker.cancel),
            ready,
            self._preview_failed,
        )

    def _preview_failed(self, error: Exception) -> None:
        cause = error.__cause__
        title, detail = self._error_copy(error, "Decklist unavailable")
        if (
            isinstance(cause, HTTPError)
            and cause.code in (403, 404)
            or isinstance(error, DeckError)
            and ("publicly available" in detail or "published a main deck" in detail)
        ):
            self.unpublished.add(self.preview_target[1].id)
            self._show_unpublished()
            self._update_actions()
        else:
            self.preview.show_message(title, detail, retry=True)

    def _show_unpublished(self) -> None:
        self.preview.show_message(
            "No public decklist", "Format Library hasn’t shared this decklist."
        )

    def _open_url(self, url: str) -> None:
        if not QDesktopServices.openUrl(QUrl(url)):
            self.ctx.notify(f"Couldn’t open {url}.")

    def _visit_event(self) -> None:
        if self.selection.event:
            self._open_url(self.selection.event.url)

    def _visit_deck(self) -> None:
        if self.preview_target:
            self._open_url(self.preview_target[1].url)

    def _refresh_saved_rows(self) -> None:
        rows = self.browser.standings
        with QSignalBlocker(rows):
            for i, deck in enumerate(self.selection.decks):
                item = rows.topLevelItem(i)
                item.setText(2, f"✓  {deck.name}" if deck.id in self.saved else deck.name)
                item.setToolTip(2, deck.name)
                item.setForeground(2, QColor(t.GREEN if deck.id in self.saved else t.INK))

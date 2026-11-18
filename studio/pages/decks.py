"""The Studio tournament browser. Network and installation work run through AppContext."""

from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QPoint, QPropertyAnimation, QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QScrollArea, QStackedWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from goat_versions import installed_whitelist
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.base import Page
from studio.pages.decks_browser import Browser, ListMessage
from studio.pages.decks_flow import (
    EventPage,
    StandingsSelection,
    format_choices,
    is_goat,
)
from studio.pages.decks_import import DeckImports
from studio.pages.decks_preview_data import prepare_preview
from studio.pages.decks_views import GoatReviewView, ImportView, PreviewView, ReceiptView
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
        self.saved: dict[int, set[Path]] = {}
        self.reads: dict[str, Job] = {}

        body = QVBoxLayout(self)
        margins(body, 0)
        self.shell = QStackedWidget()
        body.addWidget(self.shell)
        self.scrolls: dict[QWidget, QScrollArea] = {}
        self.browser = Browser()
        self.preview = PreviewView(context.tasks)
        self.import_view = ImportView()
        self.results = ReceiptView()
        self.review = GoatReviewView()
        for view in (self.browser, self.preview, self.import_view, self.results, self.review):
            view.setMaximumWidth(t.CONTENT_MAX)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
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
        b.event_message.retry.clicked.connect(lambda: self.load_events(self.page))
        b.standings_message.retry.clicked.connect(lambda: self.load_standings(self.selection.event))
        b.previous.clicked.connect(lambda: self.load_events(self.page.previous()))
        b.next.clicked.connect(lambda: self.load_events(self.page.following(self.events)))
        b.events.currentItemChanged.connect(self._event_selected)
        b.events.itemActivated.connect(lambda *_: self._focus_standings())
        b.standings.itemSelectionChanged.connect(self._decks_selected)
        b.standings.itemActivated.connect(self._activate_deck)
        b.event_link.clicked.connect(self._visit_event)
        b.select_all.clicked.connect(b.standings.selectAll)
        b.preview.clicked.connect(self.open_preview)
        b.goat.toggled.connect(self.preview.goat.setChecked)
        self.preview.goat.toggled.connect(b.goat.setChecked)
        self.preview.back.clicked.connect(self.escape)
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
        elif self.view in (self.preview, self.results, self.review):
            self.view.back.setFocus()

    def hidden(self) -> bool:
        if self.imports.job is not None:
            return False
        self._discard("artwork")
        self.results.artwork.setEnabled(True)
        return True

    def request_close(self) -> bool:
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
        if self.view in (self.preview, self.results):
            self._discard("preview", "artwork")
            self.results.artwork.setEnabled(True)
            from_preview = self.view is self.preview
            self.preview_target = None
            b = self.browser
            self._show(
                b, b.preview if from_preview and b.preview.isEnabled() else b.standings, back=True
            )
            return True
        return False

    def installation_changed(self, installation: Installation) -> None:
        self._discard(*self.reads)
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
        for control in (b.format, b.search, b.submit):
            control.setEnabled(bool(installation.path))
        b.results.setVisible(bool(installation.path))
        b.footer.setVisible(bool(installation.path))
        b.unavailable.setVisible(not installation.path)
        b.readiness.setVisible(bool(installation.path) and not installation.ready)
        if installation.path:
            folder = Path(installation.path) / "deck"
            b.destination.set_value(str(folder), f"Save to {folder.parent.name}")
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
        self.browser.event_context.setText(f"{page.format or 'All formats'} · Latest first")
        self.browser.page_number.setText(f"Page {page.number}")
        self.browser.event_message.set_copy(
            "Finding tournaments…", "Looking up Format Library.", False
        )
        self._read(
            "events",
            lambda _: self.library.events(page.format, page.query, page.number),
            self._events_loaded,
            lambda exc: self._show_error(self.browser.event_message, exc, "Search unavailable"),
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
        else:
            hint = (
                "Go back a page or try a different search."
                if self.page.previous()
                else ("Try another format or clear the event search.")
            )
            self.browser.event_message.set_copy("No tournaments found", hint, False)
        self._update_actions()

    def _event_selected(self) -> None:
        index = self.browser.events.currentIndex().row()
        if 0 <= index < len(self.events) and self.events[index] != self.selection.event:
            self.load_standings(self.events[index])

    def load_standings(self, event: Event | None) -> None:
        self._discard("standings", "goat", "preview")
        self.preview_target = None
        self.selection.open(event)
        self.goat_available = False
        b = self.browser
        if event is None or not is_goat(event):
            b.goat.setChecked(False)
        b.set_standings((), self.saved)
        b.standings_title.set_value(event.name if event else "Standings")
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
                lambda exc: self._show_error(b.standings_message, exc, "Standings unavailable"),
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
        if not decks:
            self.browser.standings_message.set_copy(
                "No public decklists",
                "Format Library has no published decks for this tournament. Try another event.",
                False,
            )
        self._update_actions()

    @staticmethod
    def _show_error(message: ListMessage, error: Exception, title: str) -> None:
        cause = error.__cause__
        if isinstance(cause, (URLError, OSError)) and not isinstance(cause, HTTPError):
            title = "Format Library is unreachable"
        detail = str(error) if isinstance(error, DeckError) else "Something went wrong. Try again."
        message.set_copy(title, detail, True)
        if message.isVisible():
            message.retry.setFocus()

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
        b.previous.setEnabled(self.page.previous() is not None)
        b.next.setEnabled(self.page.following(self.events) is not None)
        b.event_link.setEnabled(self.selection.event is not None)
        b.select_all.setEnabled(self.selection.can_select_all)
        b.preview.setEnabled(self.selection.preview is not None)
        b.save.setEnabled(bool(count and self.ctx.installation.path))
        b.save.setText(f"Add {count} decks" if count > 1 else "Add to EDOPro")
        b.selection_count.setText(f"{count} selected")
        b.goat.setVisible(self.goat_available)
        b.recent.setVisible(self.imports.receipt is not None)
        self.preview.goat.setVisible(self.goat_available)

    def _focus_standings(self) -> None:
        if self.selection.decks:
            self.browser.standings.setFocus()

    def _activate_deck(self, item: QTreeWidgetItem) -> None:
        if self.selection.preview is None and len(self.selection.selected) > 1:
            return
        self.browser.standings.setCurrentItem(item)
        self.open_preview()

    def open_preview(self) -> None:
        if (event := self.selection.event) and (deck := self.selection.preview):
            self.preview_target = event, deck
            self._load_preview()

    def _load_preview(self) -> None:
        if self.preview_target is None:
            return
        event, deck = self.preview_target
        game = Path(self.ctx.installation.path)
        self.preview.start(event, deck)
        self._show(self.preview, self.preview.back)
        self._read(
            "preview",
            lambda worker: prepare_preview(game, self.library.preview(deck), worker.cancel),
            self.preview.loaded,
            lambda exc: self._show_error(self.preview.message, exc, "Deck preview unavailable"),
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

"""Historical format profiles, tournament standings and playable deck downloads."""

from __future__ import annotations

import weakref
from functools import partial
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from edopal.core.install import RunState
from edopal.ui import theme as t
from edopal.ui.widgets import PageHeader, page_layout

from . import media
from .client import SITE, EventPage
from .deck_view import CardGrid, DeckBanner, Marker
from .http_cache import ItemUnavailable
from .service import FormatsService, _filename_part
from .widgets import (
    STYLE,
    BrowserTable,
    FormatTile,
    Hero,
    button,
    catalog_icon,
    image,
    label,
    pretty_date,
    search_icon,
    validation_summary,
)
from .widgets import layout as _layout


def _worker(method, args, ctx):
    return method(ctx, *args)


def _run_state(_ctx, installs):
    return installs.run_state()


def _scroller(content, name):
    scroll = QScrollArea()
    scroll.setObjectName(name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll


def _wrap(value, role="body"):
    widget = label(value, role)
    widget.setWordWrap(True)
    return widget


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.hide()
            widget.deleteLater()
        elif child := item.layout():
            _clear(child)


def _section(title):
    frame = QWidget()
    frame.setMinimumWidth(0)
    frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    box = _layout(frame, spacing=5)
    row = QHBoxLayout()
    heading = label(title, "heading")
    heading.setStyleSheet("font-size:21px;")
    row.addWidget(heading)
    meta = label("", "muted")
    meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    row.addWidget(meta, 1)
    box.addLayout(row)
    return frame, box, meta


class FormatsPage(QWidget):
    install_changed = Signal()

    def __init__(self, core, service=None):
        super().__init__()
        self.core = core
        self.service = service if service is not None else FormatsService(core)
        self.setObjectName("canvas")
        self.setStyleSheet(STYLE + 'QLabel[role="deckKicker"] {color:#ebc987;font-size:12px;}')
        self.started = self.previewing = self.mutating = self.undo_available = False
        self.tokens = {}
        self.handles = {}
        self.callbacks = {}
        self.choices = {}
        self.tiles = {}
        self.icons = {}
        self.art = media.FormatArt()
        self.region = "All"
        self.profile = self.events = self.selected_event = self.decks = self.deck = self.preview = None
        self.faces = {}
        self.status = ""
        self.page_number = 1
        self.standings_stale = False
        self.mode = "events"
        self.catalog_mode = "popular"
        self.user_mode = False
        self._build()
        self.install_changed.connect(self._game_changed)
        page_ref = weakref.ref(self)

        def install_changed(_install):
            page = page_ref()
            if page is not None and isValid(page):
                page.install_changed.emit()

        core.installs.subscribe(install_changed)
        self.event_timer = QTimer(self)
        self.event_timer.setSingleShot(True)
        self.event_timer.setInterval(160)
        self.event_timer.timeout.connect(self._return_events)

    def _build(self):
        self.outer = main = page_layout(self)
        self.header = PageHeader("", "Formats")
        self.header.setFixedHeight(56)
        self.header.kicker.hide()
        self.header.detail.hide()
        self.header.title.setStyleSheet("font-size:38px;")
        main.addWidget(self.header)
        self.actions = QWidget()
        actions = _layout(self.actions, QHBoxLayout, spacing=5)
        self.undo_button = button("Undo", "quiet", self._undo, "undoFormatChange")
        self.undo_button.hide()
        self.header.actions.addWidget(self.undo_button)
        self.header.actions.addWidget(self.actions)
        self.all_button = button("Install All Banlists", "", self._install_all, "installAllBanlists")
        self.all_button.setEnabled(False)
        actions.addWidget(self.all_button)
        actions.addSpacing(6)
        self.popular_button = button("Popular", "segment", partial(self._switch, "popular"), "popularFormats")
        self.library_button = button("All 128", "segment", partial(self._switch, "all"), "allFormats")
        for option in (self.popular_button, self.library_button):
            option.setCheckable(True)
            actions.addWidget(option)
        self.deck_actions = QWidget()
        actions = _layout(self.deck_actions, QHBoxLayout)
        save_as = button("Save As   ▾", "", None, "saveAs")
        self.save_menu = QMenu(save_as)
        for ext, title in (("ydk", "YDK File"), ("txt", "Text File")):
            action = self.save_menu.addAction(f"{title}\t.{ext}")
            action.setData(ext)
        self.save_menu.triggered.connect(self._export)
        save_as.setMenu(self.save_menu)
        save_as.setFixedSize(112, 35)
        actions.addWidget(save_as)
        self.download = button("Download Deck", "primary", self._save, "downloadDeck")
        self.download.setFixedSize(147, 35)
        actions.addWidget(self.download)
        self.header.actions.addWidget(self.deck_actions)
        self.deck_actions.hide()
        self.notice = _wrap("", "ok")
        self.notice.setObjectName("formatStatus")
        self.notice.hide()
        main.addWidget(self.notice)
        self.hero = Hero()
        main.addWidget(self.hero)
        self.install_button = button("Install Banlist", "primary", self._banlist_action, "installBanlist")
        self.copy_button = button("Copy Host Settings", "", self._copy, "copyHostSettings")
        self.install_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.source = button("Format Library  ↗", "source", self._open_source, "formatLibraryLink")
        for item in (self.install_button, self.copy_button, self.source):
            item.setMinimumHeight(0)
            item.setFixedHeight(31)
            self.hero.actions.addWidget(item)
        self.hero.actions.addStretch()
        self.availability = _wrap("", "muted")
        self.availability.hide()
        main.addWidget(self.availability)
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        self.browser = QWidget()
        self.stack.addWidget(self.browser)
        self.browser_layout = _layout(self.browser, spacing=18)
        self.popular, self.popular_layout, pop_meta = _section("Popular Formats")
        self.popular.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        pop_meta.setText("Oldest First")
        self.strip = QHBoxLayout()
        self.strip.setSpacing(7)
        self.popular_layout.setSpacing(8)
        self.popular_layout.addLayout(self.strip)
        self.browser_layout.addWidget(self.popular)
        self.lower = QHBoxLayout()
        self.lower.setSpacing(24)
        self.browser_layout.addLayout(self.lower, 1)
        self.catalog, self.catalog_layout, self.match_count = _section("All Formats")
        self.lower.addWidget(self.catalog, 11)
        self._build_catalog()
        self.tournament = QWidget()
        self.tables = _layout(self.tournament, QHBoxLayout, spacing=15)
        self.catalog_rule = QFrame()
        self.catalog_rule.setFixedWidth(1)
        self.catalog_rule.setStyleSheet("background:#39393c;")
        self.lower.addWidget(self.catalog_rule)
        self.lower.addWidget(self.tournament, 9)
        self.events_panel, box, self.events_meta = _section("Events")
        self.events_table = BrowserTable(("Event", "Date", "Players"), {1: 100, 2: 66})
        self.events_table.horizontalHeaderItem(2).setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.events_table.opened.connect(self._open_event)
        box.addWidget(self.events_table, 1)
        self.events_note = _wrap("Loading Formats…", "muted")
        box.addWidget(self.events_note)
        self.retry = button("Try Again", "back", self._retry_events, "retryFormats")
        self.more = button("More Events", "back", self._more_events, "moreEvents")
        for control in (self.retry, self.more):
            box.addWidget(control, 0, Qt.AlignmentFlag.AlignLeft)
            control.hide()
        self.tables.addWidget(self.events_panel, 1)
        self.divider = QFrame()
        self.divider.setStyleSheet("background:#39393c;")
        self.tables.addWidget(self.divider)
        self.standings_panel, box, self.standings_meta = _section("Standings")
        self.standings_table = BrowserTable(("Place", "Player", "Deck", ""), {0: 53, 3: 23})
        self.standings_table.setIconSize(QSize(31, 31))
        self.standings_table.opened.connect(self._open_deck)
        box.addWidget(self.standings_table, 1)
        self.standings_note = _wrap("Select an event to view its standings.", "muted")
        box.addWidget(self.standings_note)
        self.retry_standings = button("Try Again", "back", self._retry_standings, "retryStandings")
        box.addWidget(self.retry_standings, 0, Qt.AlignmentFlag.AlignLeft)
        self.retry_standings.hide()
        self.tables.addWidget(self.standings_panel, 1)
        self.detail_page = QWidget()
        self.detail_layout = _layout(self.detail_page, spacing=13)
        self.stack.addWidget(self.detail_page)
        self._layout_mode()

    def _build_catalog(self):
        heading = self.catalog_layout.itemAt(0).layout()
        self.match_count.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.match_count.setStyleSheet("background:#303032;border-radius:4px;padding:2px 6px;")
        heading.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Formats")
        self.search.setObjectName("formatSearch")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(search_icon(), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setMaximumWidth(200)
        self.search.textChanged.connect(self._filter_profiles)
        self.catalog_layout.itemAt(0).layout().addWidget(self.search)
        row = QHBoxLayout()
        self.sort = QComboBox()
        self.sort.setAccessibleName("Format Sort Order")
        self.sort.addItems(["Oldest First", "Newest First", "Name A–Z"])
        self.sort.currentIndexChanged.connect(self._filter_profiles)
        row.addWidget(self.sort)
        self.regions = QComboBox()
        self.regions.setAccessibleName("Format Region")
        self.regions.addItems(["TCG + OCG", "TCG", "OCG"])
        self.regions.currentIndexChanged.connect(self._filter_profiles)
        row.addWidget(self.regions)
        self.filters = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.filters.addLayout(row)
        self.filters.setSpacing(12)
        self.catalog_layout.addLayout(self.filters)
        self.filter_row = QHBoxLayout()
        self.filter_row.addWidget(label("Exclude Mechanics", "muted"))
        self.checks = {}
        for name in ("Synchro", "Xyz", "Pendulum", "Link"):
            c = QCheckBox(name)
            self.checks[name.lower()] = c
            c.toggled.connect(self._filter_profiles)
            self.filter_row.addWidget(c)
        self.filter_row.addStretch()
        self.filters.addLayout(self.filter_row)
        self.picker = BrowserTable(("Format", "Snapshot", "Region", "Era"), {1: 102, 2: 64, 3: 76})
        self.picker.setObjectName("formatPicker")
        self.picker.setVerticalScrollMode(self.picker.ScrollMode.ScrollPerItem)
        self.picker.verticalHeader().setDefaultSectionSize(46)
        self.picker.setIconSize(QSize(48, 32))
        self.picker.currentCellChanged.connect(self._picked)
        self.search.returnPressed.connect(self.picker.setFocus)
        self.catalog_layout.addWidget(self.picker, 1)
        self.empty_catalog = label("No Matching Formats", "muted")
        self.catalog_layout.addWidget(self.empty_catalog)
        self.empty_catalog.hide()

    def _switch(self, mode):
        if self.mode == "host":
            self._return_events()
        self.user_mode = True
        self.catalog_mode = mode
        self._layout_mode()
        if mode == "popular":
            if self.profile is None or not self.profile.get("popular"):
                if row := self.choices.get("hat"):
                    self._select(row)
        else:
            self._filter_profiles()

    def _layout_mode(self):
        all_mode = self.catalog_mode == "all"
        self.popular_button.setChecked(not all_mode)
        self.library_button.setChecked(all_mode)
        self.popular.setVisible(not all_mode)
        self.catalog.setVisible(all_mode)
        self.catalog_rule.setVisible(all_mode)
        self.tables.setDirection(QBoxLayout.Direction.TopToBottom if all_mode else QBoxLayout.Direction.LeftToRight)
        self.divider.setVisible(not all_mode)
        self.divider.setFixedWidth(1)
        self.divider.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.started:
            self.started = True
            QTimer.singleShot(0, self, self._load_profiles)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.user_mode and not self.choices:
            self.catalog_mode = "all" if self.width() >= 1400 else "popular"
            self._layout_mode()
        self.filters.setDirection(
            QBoxLayout.Direction.LeftToRight if self.width() >= 1450 else QBoxLayout.Direction.TopToBottom
        )
        self.filters.setSpacing(14 if self.width() >= 1450 else 4)
        self._page_metrics()

    def _page_metrics(self):
        wide = self.width() >= 1400
        compact = self.height() < 650
        side = 36 if wide else 32
        self.outer.setContentsMargins(side, 19 if wide else 15, side, 33 if wide else 24)
        self.outer.setSpacing(2 if self.mode == "deck" else 20 if wide else 17)
        self.hero.setFixedHeight(186 if compact else 226 if wide else 220)
        self.browser_layout.setSpacing(12 if compact else 24)

    def _job(self, lane, method, args, done, failed):
        token = self.tokens.get(lane, 0) + 1
        self.tokens[lane] = token

        self.callbacks[lane] = (done, failed)
        page_ref = weakref.ref(self)

        def apply(index, value):
            page = page_ref()
            if page is not None and isValid(page) and page.tokens[lane] == token:
                page.callbacks.pop(lane)[index](value)

        self.handles[lane] = self.core.jobs.run(
            partial(_worker, method, args),
            on_done=partial(apply, 0),
            on_error=partial(apply, 1),
            key=f"formats-{lane}-{id(self)}",
        )

    def _cancel_view(self):
        self.event_timer.stop()
        for lane in ("browse", "standings", "faces"):
            self.tokens[lane] = self.tokens.get(lane, 0) + 1
            self.callbacks.pop(lane, None)
            if handle := self.handles.pop(lane, None):
                handle.cancel()

    def _load_profiles(self):
        if not self.previewing:
            self._job("profiles", self.service.profiles, (), self._profiles_ready, self._events_error)

    def _profiles_ready(self, rows):
        if not rows:
            self._events_error("The formats pack has no profiles.")
            return
        selected = self.profile["id"] if self.profile else "hat"
        self.choices = {r["id"]: r for r in rows}
        self.library_button.setText(f"All {len(rows)}")
        _clear(self.strip)
        self.tiles = {}
        for row in sorted((r for r in rows if r.get("popular")), key=lambda r: r.get("snapshot_date") or r["date"]):
            tile = FormatTile(row)
            tile.clicked.connect(self._pick_tile)
            self.strip.addWidget(tile, 1)
            self.tiles[row["id"]] = tile
        self._select(self.choices.get(selected) or rows[0])
        self._filter_profiles()
        self._job(
            "art",
            media.load_art,
            (self.core.packs, rows, self.core.installs.current()),
            self._art_ready,
            lambda error: self.source.setToolTip(str(error)),
        )

    def _art_ready(self, art):
        self.art = art
        self.icons = {slug: catalog_icon(data) for slug, data in art.tiles.items() if data}
        for slug, tile in self.tiles.items():
            tile.pix = image(art.tiles.get(slug))
            tile.update()
        self._illustration()
        self._catalog_icons()
        self._deck_icons()
        if self.mode == "deck" and self.preview:
            self._preview_view()

    def _illustration(self):
        if self.profile:
            slug = self.profile["id"]
            self.hero.set_illustration(self.art.heroes.get(slug), self.art.captions.get(slug, ("", "")))

    def _set_region(self, name):
        self.regions.setCurrentIndex({"All": 0, "TCG": 1, "OCG": 2}[name])

    def _filter_profiles(self, *_):
        self.region = ("All", "TCG", "OCG")[self.regions.currentIndex()]
        words = self.search.text().casefold().split()
        excluded = [key for key, check in self.checks.items() if check.isChecked()]
        matches = []
        for row in self.choices.values():
            if self.region != "All" and row["region"] != self.region:
                continue
            mechanics = row.get("mechanics") or {}
            if any(mechanics.get(key, True) for key in excluded):
                continue
            haystack = " ".join(str(row.get(k) or "") for k in ("name", "region", "era", "date", "banlist")).casefold()
            if all(word in haystack for word in words):
                matches.append(row)
        matches.sort(key=lambda r: r["name"].casefold())
        if self.sort.currentIndex() != 2:
            matches.sort(key=lambda r: r.get("snapshot_date") or "9999", reverse=self.sort.currentIndex() == 1)
        self.matches = matches
        current = self.profile["id"] if self.profile else "hat"
        index = next((i for i, r in enumerate(matches) if r["id"] == current), 0)
        with QSignalBlocker(self.picker):
            self.picker.fill(
                [
                    (
                        r["name"],
                        pretty_date(r.get("snapshot_date")) if r.get("snapshot_date") else "Rolling",
                        r["region"],
                        r["era"],
                    )
                    for r in matches
                ]
            )
            if matches:
                self.picker.setCurrentCell(index, 0)
                self.picker.scrollToItem(self.picker.item(index, 0), self.picker.ScrollHint.PositionAtCenter)
        self.match_count.setText(str(len(matches)))
        self.empty_catalog.setVisible(not matches)
        self._catalog_icons()
        if self.catalog_mode == "all" and self.choices:
            if matches and (not self.profile or self.profile["id"] != matches[index]["id"]):
                self._select(matches[index])
            elif not matches:
                self._cancel_view()
                self.profile = None
                self.hero.hide()
                self.tournament.hide()

    def _catalog_icons(self):
        for i, row in enumerate(getattr(self, "matches", ())):
            if icon := self.icons.get(row["id"]):
                self.picker.item(i, 0).setIcon(icon)

    def _pick_tile(self):
        self._select(self.sender().row)

    def _picked(self, row, _col, _oldrow, _oldcol):
        if row >= 0 and self.catalog_mode == "all":
            self._select(self.matches[row])

    def _select(self, profile):
        self._cancel_view()
        self.profile = profile
        self.hero.set_profile(profile)
        self.hero.show()
        self.tournament.show()
        for slug, tile in self.tiles.items():
            tile.setChecked(slug == profile["id"])
        self._illustration()
        self.copy_button.setVisible(bool(profile.get("host")))
        self.copy_button.setEnabled(bool(profile.get("host")))
        self._notice("")
        self.events = self.selected_event = self.decks = self.deck = self.preview = None
        self.page_number = 1
        self.status = ""
        self._controls()
        self._refresh_status()
        self._browse()
        self.events_table.setRowCount(0)
        self.events_meta.clear()
        self._event_message("Loading Events…")
        self._clear_standings()
        if not self.previewing:
            self.event_timer.start()

    def _browse(self):
        self.mode = "events"
        self._page_metrics()
        self.header.title.setText("Formats")
        self.actions.show()
        self.deck_actions.hide()
        self.hero.setVisible(self.profile is not None)
        self.availability.setVisible(bool(self.profile and self.profile.get("status", "historical") != "historical"))
        self.stack.setCurrentWidget(self.browser)

    def _clear_standings(self, message="Select an event to view its standings."):
        self.standings_table.setRowCount(0)
        self.standings_meta.clear()
        self.standings_note.setText(message)
        self.standings_note.setVisible(bool(message))
        self.retry_standings.hide()

    def _event_message(self, message, retry=False):
        self.events_note.setText(message)
        self.events_note.setVisible(bool(message))
        self.retry.setVisible(retry)
        self.more.setVisible(not message and bool(self.events and self.events.has_next))

    def _events_error(self, error):
        self._event_message(str(error), True)

    def _retry_events(self):
        if not self.choices:
            self._load_profiles()
        else:
            self._load_events(self.page_number + 1 if self.events else 1)

    def _more_events(self):
        self._load_events(self.page_number + 1)

    def _return_events(self):
        self._cancel_view()
        self._browse()
        if self.events is None and self.profile:
            self._load_events(1)
        elif self.events and self.events.events and self.decks is None:
            self._load_standings(self.selected_event or self.events.events[0])

    def _load_events(self, number):
        self._event_message("Loading Events…")
        self._job(
            "browse", self.service.events, (self.profile, number), partial(self._events_ready, number), self._events_error
        )

    def _events_ready(self, number, data):
        previous = self.events_table.currentRow()
        if number > 1 and self.events:
            items = {e.code: e for e in (*self.events.events, *data.events)}
            data = EventPage(tuple(sorted(items.values(), key=lambda e: e.date, reverse=True)), data.has_next, data.stale)
        self.page_number, self.events = number, data
        self.events_table.fill(
            [(e.name, pretty_date(e.date), e.players if e.players is not None else "—") for e in data.events]
        )
        for i in range(len(data.events)):
            self.events_table.item(i, 2).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.events_meta.setText(f"{len(data.events)} Events  ·  " + ("Offline Copy" if data.stale else "Newest First"))
        self._event_message("" if data.events else "No published events for this format.")
        if data.events:
            self.events_table.selectRow(max(0, previous))
            if not self.decks and not self.previewing:
                self._open_event(max(0, previous))

    def _open_event(self, index):
        self._load_standings(self.events.events[index])

    def _retry_standings(self):
        self._load_standings(self.selected_event)

    def _load_standings(self, event):
        self.selected_event = event
        self.decks = self.deck = self.preview = None
        self._clear_standings("Loading Standings…")
        self.standings_meta.setText(event.name)
        self._job("standings", self.service.standings, (event,), self._standings_ready, self._standings_error)

    def _standings_error(self, error):
        self.standings_note.setText(str(error))
        self.standings_note.show()
        self.retry_standings.show()

    def _standings_ready(self, result):
        self.decks = result.decks
        self.standings_stale = result.stale
        if not self.selected_event:
            return
        event = self.selected_event
        meta = event.name
        if event.players:
            meta += f"  ·  {event.players} Competitors"
        if result.stale:
            meta += "  ·  Offline Copy"
        self.standings_meta.setText(meta)
        self.standings_meta.setToolTip(meta)
        self.standings_table.fill(
            [(f"{d.place:02d}" if d.place else "—", d.builder, d.name.replace(" Hero", " HERO"), "›") for d in self.decks]
        )
        self.standings_note.setText("No published decklists for this event.")
        self.standings_note.setVisible(not self.decks)
        self._deck_icons()

    def _deck_icons(self):
        for i, deck in enumerate(self.decks or ()):
            if pair := self.art.archetypes.get(deck.name.casefold()):
                self.standings_table.item(i, 2).setIcon(QIcon(str(pair[0])))

    def _standings_view(self):
        self._cancel_view()
        self._browse()
        self._controls()

    def _open_deck(self, index):
        self._load_preview(self.decks[index])

    def _detail(self, mode):
        self.mode = mode
        self._page_metrics()
        self.stack.setCurrentWidget(self.detail_page)
        self.actions.setVisible(mode == "host")
        self.deck_actions.setVisible(mode == "deck" and self.preview is not None)
        self.hero.setVisible(mode == "host")
        self.availability.hide()
        self.header.title.setText("Deck Preview" if mode == "deck" else "Formats")
        _clear(self.detail_layout)
        row = QHBoxLayout()
        if mode == "deck":
            panel = QFrame()
            panel.setObjectName("deckSummary")
            self.summary_box = _layout(panel, spacing=0)
            bar = QWidget()
            bar.setFixedHeight(32)
            bar.setLayout(row)
            row.setContentsMargins(12, 0, 16, 0)
            row.setSpacing(6)
            self.summary_box.addWidget(bar)
            self.detail_layout.addWidget(panel)
        else:
            self.detail_layout.addLayout(row)
        row.addWidget(button("‹  Back to Standings", "back", self._return_events, "browserBack"))
        row.addStretch()
        return row

    def _load_preview(self, deck):
        self.deck, self.preview = deck, None
        self.faces = {}
        self._detail("deck")
        self.detail_layout.addWidget(label("Loading Deck Preview…", "muted"))
        self.detail_layout.addStretch()
        self._job(
            "browse",
            self.service.preview,
            (self.profile, self.core.installs.current(), deck),
            self._preview_ready,
            partial(self._preview_error, deck),
        )

    def _preview_error(self, deck, error):
        if isinstance(error, ItemUnavailable):
            self.decks = tuple(d for d in self.decks if d.id != deck.id)
            from .service import Standings

            self._standings_ready(Standings(self.decks, self.standings_stale))
            self.deck = self.preview = None
            self._standings_view()
            self._notice(f"{deck.name} by {deck.builder} is no longer public.", True)
        else:
            self._detail("deck")
            self.detail_layout.addWidget(_wrap(str(error)))
            self.detail_layout.addWidget(
                button("Try Again", "back", partial(self._load_preview, deck), "retryPreview"), 0, Qt.AlignmentFlag.AlignLeft
            )
            self.detail_layout.addStretch()

    def _preview_ready(self, result):
        self.preview = result
        self._preview_view()
        codes = tuple(code for section in result.detail.printings for code, _ in section)
        self._job("faces", media.load_faces, (codes, self.core.installs.current()), self._local_faces, lambda _: None)

    def _local_faces(self, faces):
        self._faces_ready(faces)
        if self.preview and self.mode == "deck" and not self.previewing:
            missing = [code for section in self.preview.detail.printings for code, _ in section if code not in faces]
            if missing:
                self._fetch_missing(tuple(dict.fromkeys(missing)))

    def _fetch_missing(self, codes):
        batch, rest = codes[:6], codes[6:]

        def ready(faces):
            self._faces_ready(faces)
            if rest:
                self._fetch_missing(rest)

        self._job("faces", media.load_faces, (batch, None, self.service.client), ready, lambda _: None)

    def _faces_ready(self, faces):
        if self.preview and self.mode == "deck":
            self.faces.update(faces)
            self.grid.set_faces(faces)

    def _preview_view(self):
        summary = self._detail("deck")
        p = self.preview
        self.download.setEnabled(self.core.installs.current() is not None and not self.mutating)
        self.download.setToolTip("Install the deck in the selected EDOPro folder")
        if p.stale:
            summary.addWidget(label("Offline Copy", "muted"))
        if p.changes or p.issues:
            review = button(
                f"Review Printings ({len(p.changes) + len(p.issues)})", "back", self._printing_details, "printingChanges"
            )
            summary.addWidget(review)
        summary.addWidget(label(self.profile.get("banlist", "") + " Banlist   │", "muted"))
        for n, name in ((0, "Forbidden"), (1, "Limited"), (2, "Semi-Limited")):
            if n or 0 in p.limits.values():
                summary.addWidget(Marker(n))
                summary.addWidget(label(name, "muted"))
        pair = self.art.archetypes.get(self.deck.name.casefold())
        banner = DeckBanner(self.profile, self.selected_event, self.deck, p.composition, pair[1] if pair else None)
        self.summary_box.addWidget(banner)
        self.grid = CardGrid(p)
        self.grid.set_faces(self.faces)
        self.detail_layout.addWidget(self.grid, 1)

    def _show_host(self, p):
        row = self._detail("host")
        self.host_hash = label("List Hash  " + p["hash"] if p.get("hash") else "", "muted")
        row.addWidget(self.host_hash)
        return self.detail_layout

    def _install_all(self):
        self._mutation(self.service.install_all, (self.core.installs.current(),), "Installing Banlists…")

    def _export(self, action):
        if not self.preview:
            return
        ext = action.data()
        title = f"{_filename_part(self.selected_event.code)}-{_filename_part(self.deck.builder)}.{ext}"
        filetype = "YDK File (*.ydk)" if ext == "ydk" else "Text File (*.txt)"
        dialog = QFileDialog(self, "Save Deck As", "", filetype)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix(ext)
        dialog.selectFile(title)
        accepted = dialog.exec()
        files = dialog.selectedFiles()
        dialog.deleteLater()
        if not accepted or not files:
            return
        path = Path(files[0]).resolve()
        install = self.core.installs.current()
        args = (path, ext, self.deck, self.preview)
        if install and path.is_relative_to(install.root.resolve()):
            self._mutation(self.service.export, (*args, install), "Saving Deck…")
        else:
            self._job("export", self.service.export, args, self._notice, lambda error: self._notice(str(error), True))

    def _notice(self, text, error=False):
        self.notice.setText(text)
        self.notice.setProperty("role", "warning" if error else "ok")
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.setVisible(bool(text))

    def _game_changed(self):
        if not self.started:
            return
        self._notice("")
        self.status = ""
        self.undo_available = False
        self._controls()
        self._cancel_view()
        self._load_profiles()

    def _refresh_status(self):
        if self.previewing or not self.profile:
            return
        install = self.core.installs.current()
        self._job(
            "status",
            self.service.status,
            (self.profile, install),
            self._status_ready,
            lambda error: self._notice(str(error), True),
        )
        self._job("receipt", self.service.can_undo, (install,), self._undo_ready, lambda _error: self._undo_ready(False))

    def _undo_ready(self, available):
        self.undo_available = available
        self._controls()

    def _status_ready(self, status):
        self.status = status
        self._controls()
        if status == "edited":
            self._notice("A different file occupies this banlist path. Your copy is preserved.")
        elif status == "no-game":
            self._notice("Choose an EDOPro folder to save a deck or manage its banlists.")

    def _controls(self):
        kind = (self.profile or {}).get("status", "historical")
        captions = {"installed": "Banlist Installed", "update": "Update Banlist", "edited": "Local Banlist Edited"}
        enabled = self.status in ("available", "update")
        self.install_button.setVisible(kind != "unsupported")
        self.availability.setVisible(kind != "historical" and self.mode == "events")
        if kind == "stock":
            self.install_button.setText("Open Stock Banlist" if self.status == "stock" else "View Stock Banlist  ↗")
            enabled = self.status in ("stock", "stock-missing", "no-game")
            self.install_button.setToolTip(
                self.profile["stock_lflist"]
                if self.status == "stock"
                else "View the list in Project Ignis’s LFLists repository"
            )
            self.availability.setText(
                "EDOPro stock list unavailable locally" if self.status == "stock-missing" else "EDOPro stock list"
            )
        elif kind == "unsupported":
            self.availability.setText("Not playable in EDOPro yet. " + self.profile.get("reason", ""))
        else:
            self.install_button.setText(captions.get(self.status, "Install Banlist"))
            self.install_button.setToolTip("")
        self.all_button.setEnabled(self.core.installs.current() is not None and not self.mutating and bool(self.choices))
        self.install_button.setEnabled(enabled and not self.mutating)
        self.undo_button.setVisible(bool(self.undo_available))
        self.undo_button.setEnabled(not self.mutating)

    def _printing_details(self):
        entries = (*self.preview.changes, *self.preview.issues)
        box = QMessageBox(self)
        box.setWindowTitle("Historical printings")
        box.setTextFormat(Qt.TextFormat.PlainText)
        if self.preview.issues:
            box.setText("Download keeps the source deck. These cards need review before a playable copy can be saved.")
        else:
            box.setText("Download saves the published source alongside the deck with historical printings.")
        if len(entries) <= 6:
            box.setInformativeText("\n".join(entries))
        else:
            box.setInformativeText(f"{len(self.preview.changes)} printing changes · {len(self.preview.issues)} issues")
            box.setDetailedText("\n".join(entries))
        box.exec()

    def _host(self):
        if not self.profile:
            return
        self._cancel_view()
        p, host = self.profile, self.profile.get("host")
        layout = self._show_host(p)
        if digest := p.get("sha256"):
            self.host_hash.setToolTip(f"Banlist SHA-256 · {digest}")
        if not host:
            layout.addWidget(label("Deck downloads", "heading"))
            layout.addWidget(
                _wrap(
                    "Published decklists are available as source YDK files. A playable EDOPro rules profile is unavailable."
                )
            )
            layout.addStretch()
            return
        content = QWidget()
        card = _layout(content, spacing=11)
        metrics = QHBoxLayout()
        metrics.setSpacing(t.XL)
        metrics.addWidget(label(host["rule"], "heading"))
        metrics.addStretch()
        for key, title in (("lp", "Starting LP"), ("hand", "Opening Hand"), ("draw", "Draw Count")):
            stat = QVBoxLayout()
            stat.setSpacing(3)
            stat.addWidget(label(title, "kicker"))
            value = host[key]
            stat.addWidget(label(f"{value:,}" if isinstance(value, int) else value, "subheading"))
            metrics.addLayout(stat)
        card.addLayout(metrics)
        line = QFrame()
        line.setProperty("role", "line")
        line.setFixedHeight(1)
        card.addWidget(line)
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(7)
        grid.addWidget(label("Room Options", "goldKicker"), 0, 0)
        grid.addWidget(label("Known Gaps", "goldKicker"), 0, 1)
        options = [f"Forbidden List → {p['display']}", *host["options"]]
        sizes = []
        for key, title in (("main", "Main"), ("extra", "Extra"), ("side", "Side")):
            if pair := host.get(key):
                sizes.append(f"{title} {pair[0]}–{pair[1]}")
        if sizes:
            options.append("  ·  ".join(sizes))
        # The two columns have independent row heights when an option or caveat wraps.
        for column, entries in enumerate((options, p.get("known_gaps", ()))):
            items = QVBoxLayout()
            items.setSpacing(5)
            for entry in entries:
                items.addWidget(_wrap(entry, "small"))
            if column == 1 and p.get("status", "historical") == "historical":
                items.addSpacing(9)
                items.addWidget(label("Printing Coverage", "goldKicker"))
                if p.get("pool") is not None and p.get("printings") is not None:
                    items.addWidget(_wrap(f"{p['pool']:,} card identities · {p['printings']:,} printing IDs", "small"))
                counts = p.get("fallback") or {}
                ambiguous, missing = counts.get("ambiguous", 0), counts.get("unavailable", 0)
                if ambiguous or missing:
                    items.addWidget(
                        _wrap(
                            f"Fallback printings cover {ambiguous} ambiguous card histories "
                            f"and {missing} unavailable historical versions.",
                            "small",
                        )
                    )
                elif p["region"] == "TCG":
                    items.addWidget(
                        _wrap(
                            "Historical printing choices match EDOPro’s stock GOAT list."
                            if p["id"] == "goat"
                            else "No fallback printings recorded.",
                            "small",
                        )
                    )
                elif unassessed := counts.get("unassessed", 0):
                    items.addWidget(
                        _wrap(f"Historical OCG printings remain unassessed for {unassessed:,} identities.", "small")
                    )
            if column == 1:
                if note := p.get("coverage_note"):
                    items.addSpacing(6)
                    items.addWidget(_wrap(note, "small"))
                if p.get("status") == "stock" and p.get("pool") is not None:
                    items.addWidget(_wrap(f"{p['pool']:,} identities · {p['printings']:,} printings at release", "small"))
                self._deck_evidence(items, p.get("validation", {}))
            items.addStretch()
            grid.addLayout(items, 1, column)
        card.addLayout(grid)
        card.addStretch()
        layout.addWidget(_scroller(content, "readArea"), 1)

    @staticmethod
    def _deck_evidence(items, validation):
        items.addSpacing(9)
        items.addWidget(label("Published Deck Checks", "goldKicker"))
        if validation.get("decks", 0):
            failed_slots = validation.get("failed_slots", validation.get("failures", 0))
            slot_label = "slot" if failed_slots == 1 else "slots"
            items.addWidget(
                _wrap(
                    f"{validation_summary(validation)} · {validation.get('slots', 0):,} slots tested · "
                    f"{failed_slots:,} rejected {slot_label}",
                    "small",
                )
            )
        else:
            items.addWidget(_wrap(validation.get("label") or "No published decks sampled.", "small"))
        if when := validation.get("checked"):
            source = "Archived check" if validation.get("source") == "archived_tournament_check" else "Checked"
            items.addWidget(label(f"{source} {pretty_date(when)}", "muted"))
        if note := validation.get("note"):
            items.addWidget(_wrap(note, "small"))

    def _copy(self):
        if self.profile and self.profile.get("host"):
            QApplication.clipboard().setText(self.service.host_text(self.profile))
            self._host()
            self._notice("Host settings copied.")

    def _open_source(self):
        url = SITE
        if self.mode == "deck" and self.deck:
            url = self.deck.url
        elif self.mode == "standings" and self.selected_event:
            url = self.selected_event.url
        elif self.profile:
            url = f"{SITE}/formats/{quote(self.profile['name'], safe='')}"
        QDesktopServices.openUrl(QUrl(url))

    def _mutation(self, method, args, busy):
        if self.mutating:
            return
        install = self.core.installs.current()
        if install is None:
            self._notice("Choose an EDOPro folder first.", True)
            return
        self.mutating = True
        self._controls()
        if self.mode == "deck" and self.preview:
            self._preview_view()
        self._notice("Checking EDOPro…")

        def complete(message, error=False):
            self.mutating = False
            self._controls()
            if self.core.installs.current() == install:
                self._notice(message, error)
                self.core.notify(message, "error" if error else "success")
            self._refresh_status()
            if self.mode == "deck" and self.preview:
                self._preview_view()

        selected = self.deck

        def failed(error):
            complete(str(error), True)
            if (
                method == self.service.save
                and isinstance(error, ItemUnavailable)
                and self.deck == selected
                and self.mode == "deck"
            ):
                self._preview_error(selected, error)

        def checked(state):
            if self.core.installs.current() != install:
                complete("The selected EDOPro folder changed.", True)
                return
            if state == RunState.RUNNING:
                complete("Close EDOPro before changing its files.", True)
                return
            confirmed = state == RunState.UNKNOWN
            if confirmed:
                answer = QMessageBox.question(
                    self,
                    "Confirm EDOPro is closed",
                    "EDOPal could not check whether EDOPro is running. Is the game closed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.mutating = False
                    self._controls()
                    self._notice("")
                    if self.mode == "deck" and self.preview:
                        self._preview_view()
                    return
            self._notice(busy)
            self._job("write", method, (*args, confirmed), complete, failed)

        self._job("preflight", _run_state, (self.core.installs,), checked, failed)

    def _banlist_action(self):
        if self.profile.get("status") == "stock":
            rel = self.profile["stock_lflist"]
            install = self.core.installs.current()
            if install and self.status == "stock":
                url = QUrl.fromLocalFile(str(install.path(rel)))
            else:
                filename = quote(rel.rsplit("/", 1)[-1], safe="")
                url = QUrl(f"https://github.com/ProjectIgnis/LFLists/blob/master/{filename}")
            if not QDesktopServices.openUrl(url):
                self._notice(f"Open {url.toDisplayString()} to view the stock banlist.")
            return
        if self.profile.get("status") == "unsupported":
            return
        self._mutation(self.service.install, (self.profile, self.core.installs.current()), "Installing banlist…")

    def _undo(self):
        self._mutation(self.service.undo, (self.core.installs.current(),), "Restoring files…")

    def _save(self):
        if self.preview and self.deck and self.selected_event:
            self._mutation(
                self.service.save,
                (self.profile, self.core.installs.current(), self.selected_event, self.deck, self.preview),
                "Saving deck…",
            )

    def preview_state(self, name):
        """Capture the actual browser layouts with a recorded Format Library tournament."""
        from .preview_data import apply_state

        self.started = self.previewing = True
        apply_state(self, name)

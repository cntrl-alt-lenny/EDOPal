"""Historical format profiles, tournament standings and playable deck downloads."""

from __future__ import annotations

import weakref
from functools import partial
from urllib.parse import quote

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
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

from .client import SITE
from .http_cache import ItemUnavailable
from .service import FormatsService
from .widgets import (
    ID_ROLE,
    STYLE,
    BrowserTable,
    DeckThumbnail,
    Hero,
    ProfilePicker,
    button,
    label,
    pretty_date,
    validation_summary,
)


def _worker(method, args, ctx):
    return method(ctx, *args)


def _run_state(_ctx, installs):
    return installs.run_state()


def _layout(parent, layout_type=QVBoxLayout, *, margin=0, spacing=8):
    layout = layout_type(parent)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return layout


def _scroller(content, name):
    scroll = QScrollArea()
    scroll.setObjectName(name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


def _wrap(value, role="body"):
    widget = label(value, role)
    widget.setWordWrap(True)
    return widget


class FormatsPage(QWidget):
    install_changed = Signal()

    def __init__(self, core, service=None):
        super().__init__()
        self.setObjectName("canvas")
        self.setStyleSheet(STYLE)
        self.core = core
        self.service = service if service is not None else FormatsService(core)
        self.started = False
        self.previewing = False
        self.tokens = {}
        self.callbacks = {}
        self.region = "All"
        self.mutating = False
        self.undo_available = False
        self.profile = None
        self.status = ""
        self.events = self.selected_event = self.decks = self.deck = self.preview = None
        self.page_number = 1
        self.standings_stale = False
        self.mode = "events"
        self.choices = {}
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
        self.event_timer.setInterval(180)
        self.event_timer.timeout.connect(self._return_events)

    def _build(self):
        main = page_layout(self)
        header = PageHeader("Tournament archive", "Formats")
        self.match_count = header.detail
        self.source = button("Format Library  ↗", "source", self._open_source, "formatLibraryLink")
        self.source.setAccessibleName("Open Format Library in your browser")
        self.source.setFixedHeight(t.CONTROL_H)
        main.addWidget(header)
        self.columns = QHBoxLayout()
        self.columns.setSpacing(t.LG)
        main.addLayout(self.columns, 1)
        self.rail = QWidget()
        self.rail.setMinimumWidth(140)
        rail = _layout(self.rail, spacing=8)
        self.search = QLineEdit()
        self.search.setFixedHeight(t.BUTTON_H)
        self.search.setObjectName("formatSearch")
        self.search.setPlaceholderText("Search formats…")
        self.search.setAccessibleName("Search formats by name, era or date")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_changed)
        rail.addWidget(self.search)
        regions = QHBoxLayout()
        regions.setSpacing(3)
        self.regions = {}
        for name in ("All", "TCG", "OCG"):
            option = button(name, "region", self._region_clicked, f"region{name}")
            option.setCheckable(True)
            option.setFixedHeight(t.CONTROL_H)
            option.setAutoExclusive(True)
            option.setChecked(name == self.region)
            self.regions[name] = option
            regions.addWidget(option)
        rail.addLayout(regions)
        self.picker = ProfilePicker()
        self.picker.selectionModel().currentChanged.connect(self._picked)
        self.search.returnPressed.connect(self.picker.setFocus)
        rail.addWidget(self.picker, 1)
        rail.addWidget(self.source)
        self.columns.addWidget(self.rail)

        right = self.content = QWidget()
        right.setMinimumWidth(0)
        right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.right = _layout(right, spacing=12)
        self.hero = Hero()
        self.right.addWidget(self.hero)
        self.profile_actions = QWidget()
        actions = _layout(self.profile_actions, QHBoxLayout, spacing=t.SM)
        header.actions.addWidget(self.profile_actions)
        self.install_button = button("Install banlist", "primary", self._banlist_action, "installBanlist")
        self.copy_button = button("Copy host settings", "", self._copy, "copyHostSettings")
        self.undo_button = button("Undo", "quiet", self._undo, "undoFormatChange")
        self.install_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.undo_button.hide()
        actions.addWidget(self.undo_button)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.install_button)
        self.availability = _wrap("", "body")
        self.availability.setObjectName("playability")
        self.availability.hide()
        self.right.addWidget(self.availability)
        self.notice = _wrap("", "ok")
        self.notice.setObjectName("formatStatus")
        self.notice.hide()
        self.right.addWidget(self.notice)
        self.panel = QFrame()
        self.panel.setProperty("role", "panel")
        panel_layout = _layout(self.panel, margin=14, spacing=12)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.events_tab = button("Recent events", "sectionTab", self._return_events, "recentEvents")
        self.host_tab = button("Host settings", "sectionTab", self._host, "hostSettings")
        for tab in (self.events_tab, self.host_tab):
            tab.setFixedHeight(t.CONTROL_H)
            tab.setCheckable(True)
            tabs.addWidget(tab)
        tabs.addStretch()
        self.context_label = label("", "muted")
        tabs.addWidget(self.context_label)
        panel_layout.addLayout(tabs)
        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        panel_layout.addLayout(self.body_layout, 1)
        self.right.addWidget(self.panel, 1)
        self.columns.addWidget(right, 1)
        self.no_results = QWidget()
        empty_layout = _layout(self.no_results)
        empty_layout.addStretch()
        empty_layout.addWidget(label("No matching formats", "heading"), 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(
            button("Clear filters", "", self._clear_filters, "clearFormatFilters"), 0, Qt.AlignmentFlag.AlignHCenter
        )
        empty_layout.addStretch()
        self.columns.addWidget(self.no_results, 1)
        self.no_results.hide()
        self._empty("Loading formats…")

    def showEvent(self, event):
        super().showEvent(event)
        if not self.started:
            self.started = True
            QTimer.singleShot(0, self, self._load_profiles)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.right.setSpacing(t.SM if h < 650 else t.MD)
        self.rail.setFixedWidth(160 if w < 820 else 185 if w < 1000 else 216 if w < 1450 else 250)
        self.hero.setFixedHeight(135 if h < 650 else 152 if h < 800 else 190)

    def _job(self, lane, method, args, done, failed):
        token = self.tokens.get(lane, 0) + 1
        self.tokens[lane] = token

        self.callbacks[lane] = (done, failed)
        page_ref = weakref.ref(self)

        def apply(index, value):
            page = page_ref()
            if page is not None and isValid(page) and page.tokens[lane] == token:
                page.callbacks.pop(lane)[index](value)

        self.core.jobs.run(
            partial(_worker, method, args),
            on_done=partial(apply, 0),
            on_error=partial(apply, 1),
            key=f"formats-{lane}-{id(self)}",
        )

    def _cancel_view(self):
        self.event_timer.stop()
        self.tokens["browse"] = self.tokens.get("browse", 0) + 1
        self.callbacks.pop("browse", None)

    def _load_profiles(self):
        if self.previewing:
            return
        if not self.choices:
            self._empty("Loading formats…")
        self._job(
            "profiles",
            self.service.profiles,
            (),
            self._profiles_ready,
            lambda error: self._empty(str(error), self._load_profiles),
        )

    def _profiles_ready(self, rows):
        if not rows:
            self._empty("The formats pack has no available profiles.", self._load_profiles)
            return
        self.choices = {row["id"]: row for row in rows}
        self._filter_profiles(refresh=True)

    def _clear_filters(self):
        with QSignalBlocker(self.search):
            self.search.clear()
        self._set_region("All")
        self.search.setFocus()

    def _region_clicked(self, _checked=False):
        self._set_region(self.sender().text())

    def _set_region(self, region, _checked=False):
        self.region = region
        self.regions[region].setChecked(True)
        self._filter_profiles()

    def _filter_changed(self, _text):
        self._filter_profiles()

    def _filter_profiles(self, refresh=False):
        if not self.choices:
            return
        words = self.search.text().casefold().split()
        matches = []
        for row in self.choices.values():
            if self.region != "All" and row["region"] != self.region:
                continue
            haystack = " ".join(str(row.get(k) or "") for k in ("name", "region", "era", "date", "banlist")).casefold()
            if all(word in haystack for word in words):
                matches.append(row)

        # Undated, rolling lists precede the years. Keep the best-known snapshots within reach.
        def group(row):
            if row.get("popular"):
                return "Popular"
            year = (row.get("date") or "")[:4]
            return year if year.isdecimal() else "Current"

        popular_order = {"goat": 0, "edison": 1, "hat": 2, "tengu": 3, "wind-up": 4}

        def order(row):
            category = group(row)
            if category == "Popular":
                return (0, popular_order.get(row["id"], 5), row["name"])
            if category == "Current":
                return (1, 0, row["name"])
            return (2, -int(category), row["name"])

        current = self.profile["id"] if self.profile else "hat"
        with QSignalBlocker(self.picker.selectionModel()):
            selected = self.picker.populate(((group(r), r) for r in sorted(matches, key=order)), current)
        count = str(len(matches)) if len(matches) == len(self.choices) else f"{len(matches)} of {len(self.choices)}"
        self.match_count.setText(f"{count} profiles")
        self.content.setVisible(bool(selected))
        self.profile_actions.setVisible(bool(selected))
        self.no_results.setVisible(not selected)
        if not selected:
            self._cancel_view()
            self.profile = self.events = self.selected_event = self.decks = self.deck = self.preview = None
            self.mode = "events"
            return
        if self.profile is None or current != selected:
            self._select(self.choices[selected])
        elif refresh:
            self.profile = self.choices[selected]
            self.hero.set_profile(self.profile)
            self._refresh_status()
            if self.mode == "host":
                self._host()
            elif self.mode == "deck" and self.deck:
                self._load_preview(self.deck)

    def _picked(self, index, _previous):
        if slug := index.data(ID_ROLE):
            self._select(self.choices[slug])

    def _select(self, profile):
        self._cancel_view()
        self.profile = profile
        self.hero.set_profile(profile)
        self.copy_button.setVisible(bool(profile.get("host")))
        self.copy_button.setEnabled(bool(profile.get("host")))
        self.host_tab.setText("Host settings" if profile.get("host") else "Compatibility")
        self._notice("")
        self.page_number = 1
        self.events = self.selected_event = self.decks = self.deck = self.preview = None
        self.status = ""
        self._controls()
        self._refresh_status()
        self.mode = "events"
        if not self.previewing:
            self._empty("Loading recent events…")
            self.event_timer.start()

    def _body(self, mode=None, context=""):
        if mode is not None:
            self.mode = mode
        self.events_tab.setChecked(self.mode != "host")
        self.host_tab.setChecked(self.mode == "host")
        self.context_label.setText(context)
        self.context_label.setToolTip("")
        if self.body_layout.count():
            old = self.body_layout.takeAt(0).widget()
            old.hide()
            old.setParent(None)
            old.deleteLater()
        content = QWidget()
        layout = _layout(content, spacing=10)
        self.body_layout.addWidget(content)
        return layout

    def _empty(self, text, retry=None, back=None):
        layout = self._body()
        if back:
            layout.addWidget(button("‹  Back", "back", back, "browserBack"), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        message = _wrap(text)
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(message)
        if retry:
            layout.addWidget(button("Try again", "", retry, "retryFormats"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()

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
        captions = {"installed": "Banlist installed", "update": "Update banlist", "edited": "Local banlist edited"}
        enabled = self.status in ("available", "update")
        self.install_button.setVisible(kind != "unsupported")
        self.availability.setVisible(kind != "historical")
        if kind == "stock":
            self.install_button.setText("Open stock banlist" if self.status == "stock" else "View stock banlist  ↗")
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
            self.install_button.setText(captions.get(self.status, "Install banlist"))
            self.install_button.setToolTip("")
        self.install_button.setEnabled(enabled and not self.mutating)
        self.undo_button.setVisible(bool(self.undo_available))
        self.undo_button.setEnabled(not self.mutating)

    def _load_events(self, number):
        if not self.profile:
            return
        self.mode = "events"
        self._empty("Loading recent events…")
        self._job(
            "browse",
            self.service.events,
            (self.profile, number),
            partial(self._events_ready, number),
            lambda error: self._empty(
                str(error), partial(self._load_events, number), self._events_view if self.events else None
            ),
        )

    def _events_ready(self, number, events):
        self.page_number, self.events = number, events
        self._events_view()

    def _return_events(self):
        self._cancel_view()
        self.event_timer.stop()
        if self.events is None:
            self._load_events(self.page_number)
        else:
            self._events_view()

    def _events_view(self):
        if self.events is None:
            return
        data = self.events
        context = "Offline copy" if data.stale else ""
        layout = self._body("events", context)
        if data.events:
            table = BrowserTable(("EVENT", "DATE", "PLAYERS", ""), {1: 115, 2: 70, 3: 28})
            table.fill(
                [(e.name, pretty_date(e.date), e.players if e.players is not None else "—", "›") for e in data.events]
            )
            table.opened.connect(self._open_event)
            layout.addWidget(table, 1)
        else:
            layout.addWidget(_wrap("No published events on this page.", "muted"), 1)
        pager = QHBoxLayout()
        pager.addWidget(label(f"Page {self.page_number}", "muted"))
        pager.addStretch()
        prev = button("‹   Previous", "quiet", partial(self._load_events, self.page_number - 1), "previousEvents")
        nxt = button("Next   ›", "quiet", partial(self._load_events, self.page_number + 1), "nextEvents")
        prev.setEnabled(self.page_number > 1)
        nxt.setEnabled(data.has_next)
        pager.addWidget(prev)
        pager.addWidget(nxt)
        layout.addLayout(pager)

    def _open_event(self, index):
        self._load_standings(self.events.events[index])

    def _load_standings(self, event):
        self.selected_event = event
        self.decks = self.deck = self.preview = None
        self.mode = "standings"
        self._empty("Loading standings…", back=self._return_events)
        self._job(
            "browse",
            self.service.standings,
            (event,),
            self._standings_ready,
            lambda error: self._empty(str(error), partial(self._load_standings, event), self._return_events),
        )

    def _standings_ready(self, result):
        self.decks = result.decks
        self.standings_stale = result.stale
        self._standings_view()

    def _standings_view(self):
        self._cancel_view()
        event, decks = self.selected_event, self.decks
        layout = self._body("standings", "Standings · Offline copy" if self.standings_stale else "Standings")
        layout.addWidget(button("‹  All events", "back", self._return_events, "browserBack"), 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(_wrap(event.name, "heading"))
        subtitle = [pretty_date(event.date)]
        if event.players is not None:
            subtitle.append(f"{event.players} players")
        subtitle.append(f"{len(decks)} published decks")
        layout.addWidget(label("   ·   ".join(subtitle), "muted"))
        if not decks:
            layout.addWidget(_wrap("Format Library has no published decklists for this event.", "body"))
            layout.addStretch()
            return
        table = BrowserTable(("PLACE", "PLAYER", "DECK", ""), {0: 67, 3: 28})
        table.fill([(f"#{d.place}" if d.place else "—", d.builder, d.name, "›") for d in decks])
        table.opened.connect(self._open_deck)
        layout.addWidget(table, 1)

    def _open_deck(self, index):
        self._load_preview(self.decks[index])

    def _load_preview(self, deck):
        self.deck, self.preview = deck, None
        self.mode = "deck"
        self._empty("Loading deck preview…", back=self._standings_view)
        self._job(
            "browse",
            self.service.preview,
            (self.profile, self.core.installs.current(), deck),
            self._preview_ready,
            partial(self._preview_error, deck),
        )

    def _preview_error(self, deck, error):
        if isinstance(error, ItemUnavailable):
            self.decks = tuple(item for item in self.decks if item.id != deck.id)
            self.deck = self.preview = None
            self._standings_view()
            self._notice(f"{deck.name} by {deck.builder} is no longer public.", True)
        else:
            self._empty(str(error), partial(self._load_preview, deck), self._standings_view)

    def _preview_ready(self, result):
        self.preview = result
        self._preview_view()

    def _preview_view(self):
        preview, deck = self.preview, self.deck
        layout = self._body("deck", "Offline copy" if getattr(preview, "stale", False) else "Deck preview")
        layout.addWidget(button("‹  Standings", "back", self._standings_view, "browserBack"), 0, Qt.AlignmentFlag.AlignLeft)
        title_row = QHBoxLayout()
        title_row.addWidget(_wrap(deck.name, "heading"), 1)
        caption = "Download source deck" if self.profile.get("status") == "unsupported" else "Download deck"
        save = button(caption, "primary", self._save, "downloadDeck")
        save.setEnabled(self.core.installs.current() is not None and not self.mutating)
        title_row.addWidget(save)
        layout.addLayout(title_row)
        caption = [deck.builder, self.selected_event.name]
        if deck.place:
            caption.append(f"Place #{deck.place}")
        layout.addWidget(_wrap("   ·   ".join(caption), "muted"))
        details = QHBoxLayout()
        details.setSpacing(16)
        pixmap = QPixmap()
        if preview.thumbnail and pixmap.loadFromData(preview.thumbnail):
            details.addWidget(DeckThumbnail(pixmap), 3)
        else:
            layout.addWidget(label("Deck thumbnail unavailable.", "muted"))
        list_area = QWidget()
        card_layout = _layout(list_area, spacing=8)
        nav = QHBoxLayout()
        nav.setSpacing(4)
        stack = QStackedWidget()
        for i, (section, names) in enumerate(zip(("Main", "Extra", "Side"), preview.detail.sections, strict=True)):
            count = sum(n for _, n in names)
            tab = button(f"{section}  {count}", "deckSection", partial(stack.setCurrentIndex, i), f"{section.lower()}Deck")
            tab.setCheckable(True)
            tab.setAutoExclusive(True)
            tab.setChecked(i == 0)
            nav.addWidget(tab)
            listing = QWidget()
            cards = _layout(listing, spacing=4)
            for name, quantity in names:
                row = QHBoxLayout()
                row.setSpacing(7)
                amount = label(f"×{quantity}", "quantity")
                amount.setFixedWidth(23)
                row.addWidget(amount)
                row.addWidget(_wrap(name), 1)
                cards.addLayout(row)
            if not names:
                cards.addWidget(label(f"No {section.lower()} deck cards.", "muted"))
            cards.addStretch()
            stack.addWidget(_scroller(listing, "readArea"))
        nav.addStretch()
        card_layout.addLayout(nav)
        card_layout.addWidget(stack, 1)
        details.addWidget(list_area, 4)
        layout.addLayout(details, 1)
        changes, issues = tuple(preview.changes), tuple(preview.issues)
        if changes or issues:
            note = button(
                f"Review {len(changes)} printing changes · {len(issues)} issues",
                "back",
                self._printing_details,
                "printingChanges",
            )
            note.setToolTip("\n".join((*changes, *issues)))
            layout.addWidget(note, 0, Qt.AlignmentFlag.AlignLeft)

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
        layout = self._body("host", "List hash  " + p["hash"] if p.get("hash") else "")
        if digest := p.get("sha256"):
            self.context_label.setToolTip(f"Banlist SHA-256 · {digest}")
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
        for key, title in (("lp", "STARTING LP"), ("hand", "OPENING HAND"), ("draw", "DRAW COUNT")):
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
        grid.addWidget(label("ROOM OPTIONS", "goldKicker"), 0, 0)
        grid.addWidget(label("KNOWN GAPS", "goldKicker"), 0, 1)
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
                items.addWidget(label("PRINTING COVERAGE", "goldKicker"))
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
        items.addWidget(label("PUBLISHED DECK CHECKS", "goldKicker"))
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

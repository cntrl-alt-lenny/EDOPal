"""Native browser for Format Library tournaments and public decklists."""

from __future__ import annotations

import contextlib
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from config import APP_NAME
from goat_versions import GoatVersions, installed_aliases, installed_whitelist
from tournament_decks import (
    PAGE_SIZE,
    SITE,
    Deck,
    DeckError,
    Event,
    FormatLibrary,
    local_deck_printings,
    match_local_printings,
    save_deck,
)

ALL_FORMATS = "All formats"
STARTER_FORMATS = (ALL_FORMATS, "Goat", "Edison", "Tengu", "HAT", "Perfect Circle")
GOAT_ROOM_SETTINGS = (
    "In the EDOPro room, set Forbidden List to “2005.4 GOAT”, "
    "Rule to “GOAT” and Allowed Cards to “Anything goes”."
)
GOAT_OPTION_HINT = "Matches historical cards to your 2005.4 GOAT list."


def open_tournament_decks(parent, game_path, *, on_sync_artwork=None):
    """Open the tournament browser for the selected EDOPro installation."""
    owner = parent.winfo_toplevel()
    if not game_path:
        raise DeckError("Choose an EDOPro installation before browsing tournament decks.")
    target = Path(game_path).resolve()
    window = owner.children.get("tournament_decks")
    if isinstance(window, TournamentWindow) and window.winfo_exists():
        if window.game_path == target:
            window.on_sync_artwork = on_sync_artwork
            window.deiconify()
            window.lift()
            window.focus_set()
            return window
        window.destroy()
    return TournamentWindow(owner, target, on_sync_artwork)


def _table(parent, columns, *, multiple=False, height=8):
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        frame,
        columns=[col[0] for col in columns],
        show="headings",
        selectmode="extended" if multiple else "browse",
        height=height,
        style="Library.Treeview",
    )
    for key, label, width, stretch in columns:
        tree.column(key, width=width, minwidth=32 if not stretch else 65, stretch=stretch)
        tree.heading(key, text=label, anchor="w")
    tree.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(
        frame, orient="vertical", command=tree.yview, style="Library.Vertical.TScrollbar"
    )
    scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scroll.set)
    return frame, tree


def _style_tables(top):
    style = ttk.Style(top)
    bg = style.lookup("TFrame", "background") or "#151615"
    surface = style.lookup("Card.TFrame", "background") or "#242521"
    text = style.lookup("TLabel", "foreground") or "#f7f4ec"
    dim = style.lookup("Status.TLabel", "foreground") or "#cecac1"
    edge = style.lookup("Card.TFrame", "bordercolor") or "#3b3c36"
    accent = style.lookup("Aqua.TButton", "background") or "#8ae8c2"
    selected_fg = style.lookup("Aqua.TButton", "foreground") or "#172821"
    row_height = max(29, round(top.winfo_fpixels("0.26i")))
    style.configure(
        "Library.TEntry",
        fieldbackground=surface,
        foreground=text,
        insertcolor=text,
        bordercolor=edge,
        lightcolor=surface,
        darkcolor=surface,
        padding=5,
    )
    style.map("Library.TEntry", bordercolor=[("focus", accent)])
    style.configure(
        "Library.TCombobox", padding=4, arrowsize=12, arrowcolor=text, background=surface
    )
    style.configure(
        "Library.Vertical.TScrollbar",
        background=edge,
        troughcolor=bg,
        arrowcolor=dim,
        darkcolor=edge,
        lightcolor=edge,
        bordercolor=bg,
        arrowsize=10,
        width=12,
    )
    style.map(
        "Library.Vertical.TScrollbar", background=[("active", dim)], arrowcolor=[("active", text)]
    )
    style.configure(
        "Library.Treeview",
        background=surface,
        fieldbackground=surface,
        foreground=text,
        rowheight=row_height,
        borderwidth=0,
    )
    style.map(
        "Library.Treeview",
        background=[("selected", accent)],
        foreground=[("selected", selected_fg)],
    )
    style.configure(
        "Library.Treeview.Heading",
        background=surface,
        foreground=dim,
        bordercolor=edge,
        lightcolor=surface,
        darkcolor=surface,
        padding=(5, 5),
    )
    style.map("Library.Treeview.Heading", background=[("active", surface)])
    top.configure(bg=bg)


class TournamentWindow(tk.Toplevel):
    def __init__(self, parent, game_path, on_sync_artwork=None):
        super().__init__(parent, name="tournament_decks")
        self.game_path = game_path
        self.on_sync_artwork = on_sync_artwork
        self.import_paths = ()
        self.last_selection = ()
        self.library = FormatLibrary()
        self.title(f"Tournament decks · {APP_NAME}")
        self.transient(parent)
        self.minsize(min(690, self.winfo_screenwidth()), min(485, self.winfo_screenheight() - 60))
        width = min(1020, self.winfo_screenwidth() - 24)
        height = min(660, self.winfo_screenheight() - 80)
        self.geometry(f"{width}x{height}")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        _style_tables(self)

        self.closed = threading.Event()
        self.jobs = queue.Queue()
        self.messages = queue.Queue()
        self.pending = {}
        self.page = 1
        self.page_full = False
        self.query = ("Goat", "")
        self.events: dict[str, Event] = {}
        self.decks: dict[str, Deck] = {}
        self.event = None
        self.saved = set()
        self.importing = False
        self.use_goat = tk.BooleanVar(self, False)
        self.goat_hint = tk.StringVar(self, GOAT_OPTION_HINT)
        self.use_goat.trace_add(
            "write",
            lambda *_: self.goat_hint.set(
                GOAT_ROOM_SETTINGS if self.use_goat.get() else GOAT_OPTION_HINT
            ),
        )
        self.goat_available = False
        self.goat_receipt = ()
        self.cancel_import = threading.Event()
        self.preview_popup = None
        self._build()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Destroy>", self._on_destroy, add="+")
        threading.Thread(target=self._worker, name="Format Library", daemon=True).start()
        self.after(40, self._poll)
        self._load_events()
        self._submit("formats", self.library.formats, self._set_formats, lambda _error: None)

    def _build(self):
        body = ttk.Frame(self, padding=(20, 14))
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Tournament decks", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            header,
            text="Format Library ↗",
            style="Quiet.TButton",
            command=lambda: webbrowser.open(f"{SITE}/events"),
        ).grid(row=0, column=1, sticky="e")
        ttk.Label(
            body,
            text="Browse tournament results and add a decklist to your EDOPro deck editor.",
            style="Status.TLabel",
            wraplength=650,
        ).grid(row=1, column=0, sticky="w", pady=(3, 10))

        controls = ttk.Frame(body)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Format", style="Faint.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Event name or code", style="Faint.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        self.format = tk.StringVar(self, "Goat")
        self.format_box = ttk.Combobox(
            controls,
            textvariable=self.format,
            values=STARTER_FORMATS,
            width=18,
            state="readonly",
            style="Library.TCombobox",
        )
        self.format_box.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.format_box.bind("<<ComboboxSelected>>", lambda _e: self._search())
        self.search_text = tk.StringVar(self)
        entry = ttk.Entry(controls, textvariable=self.search_text, width=20, style="Library.TEntry")
        entry.grid(row=1, column=1, sticky="ew", padx=(0, 6))
        entry.bind("<Return>", lambda _e: self._search())
        ttk.Button(controls, text="Search", command=self._search).grid(row=1, column=2)

        middle = ttk.Frame(body)
        middle.grid(row=3, column=0, sticky="nsew")
        middle.columnconfigure(0, weight=1, uniform="list")
        middle.columnconfigure(1, weight=1, uniform="list")
        middle.rowconfigure(0, weight=1)
        left = ttk.Frame(middle)
        right = ttk.Frame(middle)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        for panel in (left, right):
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(2, weight=1)
        ttk.Label(left, text="Tournaments", style="Brand.TLabel").grid(row=0, column=0, sticky="w")
        self.list_status = tk.StringVar(self)
        self.deck_status = tk.StringVar(self)
        ttk.Label(left, textvariable=self.list_status, style="Status.TLabel", wraplength=295).grid(
            row=1, column=0, sticky="w", pady=(1, 7)
        )
        ttk.Label(right, text="Top decklists", style="Brand.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(right, textvariable=self.deck_status, style="Status.TLabel", wraplength=295).grid(
            row=1, column=0, sticky="w", pady=(1, 7)
        )
        event_frame, self.event_list = _table(
            left,
            [
                ("event", "Event", 215, True),
                ("date", "Date", 88, False),
            ],
        )
        event_frame.grid(row=2, column=0, sticky="nsew")
        self.event_list.bind("<<TreeviewSelect>>", lambda _e: self._select_event())
        deck_frame, self.deck_list = _table(
            right,
            [
                ("place", "Place", 55, False),
                ("deck", "Deck", 134, True),
                ("player", "Player", 112, True),
            ],
            multiple=True,
        )
        deck_frame.grid(row=2, column=0, sticky="nsew")
        self.deck_list.bind("<<TreeviewSelect>>", self._deck_selection_changed)
        self.deck_list.bind("<Double-1>", self._double_click)
        self.deck_list.bind("<Return>", lambda _e: self._preview())
        self.deck_list.bind("<Control-a>", self._select_all)
        self.deck_list.bind("<Command-a>", self._select_all)
        pages = ttk.Frame(left)
        pages.grid(row=3, column=0, sticky="ew", pady=(5, 0))
        pages.columnconfigure(1, weight=1)
        self.prev = ttk.Button(
            pages, text="‹ Previous", style="Quiet.TButton", command=lambda: self._move(-1)
        )
        self.prev.grid(row=0, column=0, sticky="w")
        self.page_label = ttk.Label(pages, style="Faint.TLabel")
        self.page_label.grid(row=0, column=1)
        self.next = ttk.Button(
            pages, text="Next ›", style="Quiet.TButton", command=lambda: self._move(1)
        )
        self.next.grid(row=0, column=2, sticky="e")
        links = ttk.Frame(right)
        links.grid(row=3, column=0, sticky="ew", pady=(5, 0))
        links.columnconfigure(0, weight=1)
        self.event_link = ttk.Button(
            links, text="View event ↗", style="Quiet.TButton", command=self._visit_event
        )
        self.event_link.grid(row=0, column=0, sticky="w")
        self.select_all = ttk.Button(
            links, text="Select all", style="Quiet.TButton", command=self._select_all
        )
        self.select_all.grid(row=0, column=1, sticky="e")
        self.goat_panel = ttk.Frame(body)
        self.goat_panel.columnconfigure(1, weight=1)
        self.goat_switch = ttk.Checkbutton(
            self.goat_panel,
            text="Use installed GOAT card versions",
            variable=self.use_goat,
            style="Gallery.TCheckbutton",
        )
        self.goat_switch.grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(
            self.goat_panel,
            textvariable=self.goat_hint,
            style="Faint.TLabel",
            wraplength=310,
        ).grid(row=0, column=1, sticky="w")
        self.goat_changes = ttk.Button(
            self.goat_panel, text="Review matches", command=self._review_goat
        )
        ttk.Separator(body).grid(row=5, column=0, sticky="ew", pady=(8, 8))
        self.notice = tk.StringVar(self)
        self.feedback = ttk.Label(
            body, textvariable=self.notice, style="Status.TLabel", wraplength=670
        )
        self.feedback.grid(row=6, column=0, sticky="ew")
        footer = ttk.Frame(body)
        footer.grid(row=7, column=0, sticky="ew", pady=(7, 0))
        footer.columnconfigure(0, weight=1)
        self.preview_button = ttk.Button(footer, text="Preview deck", command=self._preview)
        self.preview_button.grid(row=0, column=2, padx=(0, 8))
        self.add_button = ttk.Button(
            footer, text="Add to EDOPro", style="Aqua.TButton", command=self._import
        )
        self.add_button.grid(row=0, column=3)
        self.sync_button = ttk.Button(footer, text="Sync deck artwork", command=self._sync_artwork)
        self.destination = ttk.Label(
            footer,
            text=f"Deck folder   {self.game_path / 'deck'}",
            style="Faint.TLabel",
            wraplength=370,
        )
        self.destination.grid(row=0, column=0, sticky="w", padx=(0, 12))
        footer.bind(
            "<Configure>", lambda e: self.destination.configure(wraplength=max(110, e.width - 420))
        )
        self._update_buttons()

    def _on_destroy(self, evt):
        if evt.widget is self:
            self.closed.set()
            self.jobs.put(None)

    def _worker(self):
        while not self.closed.is_set():
            item = self.jobs.get()
            if item is None:
                return
            cancel, job, finish, fail = item
            if cancel.is_set():
                continue
            try:
                result = job()
                handler = finish
            except (DeckError, OSError) as exc:
                result = str(exc)
                handler = fail
            except Exception:
                result = "Format Library returned unfamiliar data. Open the source or try again."
                handler = fail
            self.messages.put((cancel, handler, result))

    def _poll(self):
        while not self.closed.is_set():
            try:
                cancel, handler, result = self.messages.get_nowait()
            except queue.Empty:
                self.after(55, self._poll)
                return
            if not cancel.is_set():
                handler(result)

    def _cancel(self, key):
        old = self.pending.pop(key, None)
        if old:
            old.set()

    def _submit(self, key, job, finish, fail):
        self._cancel(key)
        token = self.pending[key] = threading.Event()
        self.jobs.put((token, job, finish, fail))
        return token

    def _set_formats(self, names):
        favorites = [f for f in STARTER_FORMATS[1:] if f in names]
        rest = sorted(set(names) - set(favorites), key=str.casefold)
        self.format_box.configure(values=(ALL_FORMATS, *favorites, *rest))

    def _message(self, text, *, error=False, success=False):
        self.feedback.configure(
            style="Bad.TLabel" if error else "Good.TLabel" if success else "Status.TLabel"
        )
        self.notice.set(text)

    def _search(self):
        self.query = (
            "" if self.format.get() == ALL_FORMATS else self.format.get(),
            self.search_text.get().strip(),
        )
        self.page = 1
        self._load_events()

    def _move(self, step):
        self.page = max(1, self.page + step)
        self._load_events()

    def _load_events(self):
        self._clear_sync()
        self._cancel("standings")
        self._cancel("preview")
        self.event = None
        self.goat_panel.grid_remove()
        self.events.clear()
        self.decks.clear()
        self.event_list.delete(*self.event_list.get_children())
        self.deck_list.delete(*self.deck_list.get_children())
        self.page_full = False
        self.page_label.configure(text=f"Page {self.page}")
        self.list_status.set("Finding tournaments…")
        self.deck_status.set("Choose a tournament")
        if not self.importing:
            self._message("")
        self._update_buttons()
        fmt, query = self.query
        page = self.page
        self._submit(
            "events",
            lambda: self.library.events(fmt, query, page),
            self._show_events,
            self._events_failed,
        )

    def _events_failed(self, error):
        self.list_status.set("Search unavailable")
        self._message(f"{error} Use Search to retry.", error=True)

    def _show_events(self, events):
        self.page_full = len(events) == PAGE_SIZE
        for i, event in enumerate(events):
            iid = str(i)
            self.events[iid] = event
            self.event_list.insert("", "end", iid=iid, values=(event.name, event.date or "—"))
        self.list_status.set(
            f"{self.query[0] or ALL_FORMATS} · latest first"
            if events
            else "No tournaments on this page"
        )
        self._update_buttons()
        if events:
            self.event_list.selection_set("0")
            self.event_list.focus("0")
        elif not self.importing:
            self._message(
                "Go back a page or try a different event name."
                if self.page > 1
                else "Try another format or clear the event search."
            )

    def _select_event(self):
        selected = self.event_list.selection()
        event = self.events.get(selected[0]) if selected else None
        if event == self.event:
            return
        self._clear_sync()
        self._cancel("preview")
        self._cancel("standings")
        self.decks.clear()
        self.deck_list.delete(*self.deck_list.get_children())
        self.event = event
        self.goat_panel.grid_remove()
        self.goat_changes.grid_remove()
        self.goat_receipt = ()
        self.goat_available = False
        self._cancel("goat-list")
        if event is None or event.format.casefold() != "goat":
            self.use_goat.set(False)
        self._update_buttons()
        if event is None:
            return
        self.deck_status.set(f"{event.code} · loading…")
        if not self.importing:
            self._message(event.name)
        self._submit(
            "standings", lambda: self.library.standings(event), self._show_decks, self._decks_failed
        )
        if event.format.casefold() == "goat":
            self._submit(
                "goat-list",
                lambda: bool(installed_whitelist(self.game_path)),
                self._show_goat_option,
                lambda _message: self._show_goat_option(False),
            )

    def _show_goat_option(self, available):
        self.goat_available = bool(
            available and self.event and self.event.format.casefold() == "goat"
        )
        if self.goat_available:
            self.goat_panel.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        else:
            self.use_goat.set(False)
            self.goat_panel.grid_remove()

    def _review_goat(self):
        if not self.goat_receipt:
            return
        popup = tk.Toplevel(self)
        popup.title("GOAT version matches")
        popup.transient(self)
        popup.geometry(
            f"{min(780, self.winfo_screenwidth() - 40)}x{min(492, self.winfo_screenheight() - 80)}"
        )
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(0, weight=1)
        popup.bind("<Escape>", lambda _evt: popup.destroy())
        box = ttk.Frame(popup, padding=16)
        box.grid(sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)
        ttk.Label(box, text="GOAT version matches", style="Title.TLabel").grid(row=0, sticky="w")
        ttk.Label(box, text=GOAT_ROOM_SETTINGS, style="Status.TLabel", wraplength=630).grid(
            row=1, sticky="w", pady=(2, 10)
        )
        container, table = _table(
            box,
            [
                ("name", "Card", 274, True),
                ("old", "Input ID", 98, False),
                ("new", "Saved ID", 98, False),
                ("reason", "Result", 160, True),
            ],
        )
        container.grid(row=2, column=0, sticky="nsew")
        for row in self.goat_receipt:
            table.insert("", "end", values=row)
        ttk.Button(box, text="Done", style="Aqua.TButton", command=popup.destroy).grid(
            row=3, sticky="e", pady=(10, 0)
        )

    def _decks_failed(self, error):
        self.deck_status.set("Standings unavailable")
        self._message(f"{error} Select Search to retry.", error=True)

    def _show_decks(self, decks):
        if not self.event:
            return
        for deck in decks:
            iid = str(deck.id)
            self.decks[iid] = deck
            self.deck_list.insert("", "end", iid=iid, values=self._deck_row(deck))
        count = len(decks)
        info = f"{self.event.format} · {self.event.code}"
        if self.event.players:
            info += f" · {self.event.players} players"
        self.deck_status.set(info)
        if decks:
            iid = str(decks[0].id)
            self.deck_list.selection_set(iid)
            self.deck_list.focus(iid)
        elif not self.importing:
            self._message(
                "Format Library has no public decklists for this tournament. Try another event."
            )
        self.select_all.configure(text=f"Select all {count}" if count > 1 else "Select all")
        self._update_buttons()

    def _deck_row(self, deck):
        name = f"✓ {deck.name}" if deck.id in self.saved else deck.name
        return (deck.place or "—", name, deck.builder)

    def _update_buttons(self):
        n = len(self.deck_list.selection())
        self.goat_switch.state(["disabled"] if self.importing else ["!disabled"])
        for button, enabled in (
            (self.prev, self.page > 1),
            (self.next, self.page_full),
            (self.select_all, len(self.decks) > 1),
            (self.event_link, self.event is not None),
            (self.preview_button, n == 1 and not self.importing),
            (self.add_button, not self.cancel_import.is_set() if self.importing else n > 0),
        ):
            button.state(["!disabled"] if enabled else ["disabled"])
        if self.importing:
            self.add_button.configure(
                text="Stopping…" if self.cancel_import.is_set() else "Cancel import"
            )
        else:
            self.add_button.configure(text=f"Add {n} decks" if n > 1 else "Add to EDOPro")

    def _deck_selection_changed(self, _evt):
        selected = self.deck_list.selection()
        if selected != self.last_selection:
            self.last_selection = selected
            self._cancel("preview")
            self._clear_sync()
        self._update_buttons()

    def _select_all(self, _evt=None):
        self.deck_list.selection_set(self.deck_list.get_children())
        return "break"

    def _visit_event(self):
        if self.event:
            webbrowser.open(self.event.url)

    def _double_click(self, evt):
        iid = self.deck_list.identify_row(evt.y)
        if iid and not self.importing:
            self.deck_list.selection_set(iid)
            self._deck_selection_changed(None)
            self._preview()

    def _preview(self):
        selected = self.deck_list.selection()
        if self.importing or len(selected) != 1 or not self.event:
            return
        deck = self.decks[selected[0]]
        event = self.event
        self._message(f"Loading {deck.builder}’s {deck.name}…")
        self._submit(
            "preview",
            lambda: self.library.preview(deck),
            lambda data: self._show_preview(event, deck, data),
            lambda message: self._message(message, error=True),
        )

    def _show_preview(self, event, deck, preview):
        if self.preview_popup and self.preview_popup.winfo_exists():
            self.preview_popup.destroy()
        self._message(event.name)
        popup = self.preview_popup = tk.Toplevel(self)
        popup.title(f"{deck.name} · {deck.builder}")
        popup.transient(self)
        popup.geometry(
            f"{min(850, self.winfo_screenwidth() - 40)}x{min(555, self.winfo_screenheight() - 80)}"
        )
        popup.minsize(400, 330)
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(0, weight=1)
        popup.bind("<Escape>", lambda _e: popup.destroy())
        box = ttk.Frame(popup, padding=18)
        box.grid(sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(3, weight=1)
        ttk.Label(box, text=deck.name, style="Title.TLabel", wraplength=620).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        subtitle = f"{deck.builder}   ·   {event.format} / {event.code}"
        if deck.place:
            subtitle += f"   ·   Place {deck.place}"
        ttk.Label(box, text=subtitle, style="Status.TLabel", wraplength=510).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(3, 12)
        )
        picker = ttk.Frame(box)
        picker.grid(row=2, sticky="w", pady=(0, 5))
        container, cards = _table(box, [("amount", "Qty", 44, False), ("card", "Card", 275, True)])
        container.grid(row=3, sticky="nsew")
        selected = tk.IntVar(popup, 0)

        def show():
            cards.delete(*cards.get_children())
            for name, amount in preview.sections[selected.get()]:
                cards.insert("", "end", values=(amount, name))

        for index, (name, entries) in enumerate(
            zip(("Main", "Extra", "Side"), preview.sections, strict=True)
        ):
            count = sum(n for _, n in entries)
            ttk.Radiobutton(
                picker,
                text=f"{name}   {count}",
                variable=selected,
                value=index,
                style="Scope.TRadiobutton",
                command=show,
            ).grid(row=0, column=index)
        show()
        art = ttk.Frame(box, padding=(16, 0, 0, 0))
        ttk.Label(art, text="Published main deck", style="Brand.TLabel").grid(
            row=0, column=0, sticky="w", pady=(2, 8)
        )
        grid_image = ttk.Label(art)
        grid_image.grid(row=1, column=0, sticky="nw")

        def size_art(_evt=None):
            if getattr(grid_image, "image", None) and popup.winfo_width() >= 710:
                art.grid(row=2, column=1, rowspan=2, sticky="nw")
            else:
                art.grid_remove()

        def show_art(data):
            if not popup.winfo_exists():
                return
            try:
                photo = tk.PhotoImage(data=data, format="png", master=popup)
            except tk.TclError:
                return
            factor = max((photo.width() + 359) // 360, (photo.height() + 339) // 340, 1)
            if factor > 1:
                photo = photo.subsample(factor)
            grid_image.configure(image=photo)
            grid_image.image = photo
            size_art()

        popup.bind("<Configure>", lambda evt: size_art() if evt.widget is popup else None, add="+")
        self._submit(
            "thumbnail", lambda: self.library.thumbnail(deck), show_art, lambda _error: None
        )
        actions = ttk.Frame(box)
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        preview_goat = self.goat_available and event.format.casefold() == "goat"
        if preview_goat:
            ttk.Checkbutton(
                actions,
                text="Use installed GOAT card versions",
                variable=self.use_goat,
                style="Gallery.TCheckbutton",
            ).grid(row=0, column=0, sticky="w", pady=(0, 5))
            ttk.Label(
                actions,
                textvariable=self.goat_hint,
                style="Faint.TLabel",
                wraplength=300,
            ).grid(row=0, column=1, sticky="w", pady=(0, 5))
        action_row = 1 if preview_goat else 0
        ttk.Button(
            actions,
            text="Source decklist ↗",
            style="Quiet.TButton",
            command=lambda: webbrowser.open(deck.url),
        ).grid(row=action_row, column=0, sticky="w")

        def add():
            popup.destroy()
            self._start_import(event, (deck,), goat_requested=preview_goat and self.use_goat.get())

        ttk.Button(actions, text="Add to EDOPro", style="Aqua.TButton", command=add).grid(
            row=action_row, column=1
        )
        popup.focus_set()

    def _import(self):
        if self.importing:
            self.cancel_import.set()
            self._update_buttons()
            return
        decks = tuple(self.decks[iid] for iid in self.deck_list.selection() if iid in self.decks)
        if self.event and decks:
            self._start_import(
                self.event, decks, goat_requested=self.goat_available and self.use_goat.get()
            )

    def _start_import(self, event, decks, *, goat_requested=False):
        if self.importing:
            return
        use_goat = goat_requested and event.format.casefold() == "goat"
        self.goat_receipt = ()
        self.goat_changes.grid_remove()
        self.importing = True
        self._clear_sync()
        self.cancel_import.clear()
        self._cancel("preview")
        self._update_buttons()
        self._message(f"Adding {len(decks)} decklist{'s' if len(decks) != 1 else ''}…")

        def import_selected():
            saved = []
            error = ""
            local = local_deck_printings(self.game_path)
            matched = 0
            missing = set()
            goat = None
            goat_changed = 0
            goat_review = set()
            goat_receipt = set()
            if use_goat:
                limits = installed_whitelist(self.game_path)
                if not limits or not local:
                    raise DeckError(
                        "EDOPro needs its 2005.4 GOAT whitelist and card databases for this option. Turn it off to save the standard list."
                    )
                goat = GoatVersions(limits, local, installed_aliases(self.game_path, local))
            for i, deck in enumerate(decks, 1):
                if self.closed.is_set() or self.cancel_import.is_set():
                    break
                try:
                    note = f"Adding {i} of {len(decks)} · {deck.name} by {deck.builder}"
                    self.messages.put((self.closed, self._message, note))
                    cards = self.library.download(deck)
                    changed = 0
                    if local and any(
                        int(code) not in local for section in cards for code in section
                    ):
                        with contextlib.suppress(DeckError):
                            cards, changed = match_local_printings(
                                cards, local, self.library.preview(deck)
                            )
                    conversion = goat.convert(cards) if goat else None
                    if conversion:
                        cards = conversion.sections
                    if self.closed.is_set() or self.cancel_import.is_set():
                        break
                    path, added = save_deck(
                        self.game_path, event, deck, cards, goat_versions=use_goat
                    )
                    saved.append((deck, path, added))
                    matched += changed
                    if conversion:
                        goat_changed += conversion.changed
                        goat_review.update(conversion.review)
                        for code, target in conversion.replacements.items():
                            goat_receipt.add((local[code], code, int(target), "Historical version"))
                        for code, reason in conversion.review.items():
                            goat_receipt.add(
                                (
                                    local.get(code, "Unknown card"),
                                    code,
                                    code,
                                    "Retained · " + reason,
                                )
                            )
                    if local:
                        missing.update(
                            code for section in cards for code in section if int(code) not in local
                        )
                except (OSError, DeckError) as exc:
                    error = f"{deck.name} · {deck.builder}: {exc}"
                    break
            notes = []
            if matched:
                notes.append(
                    f"Matched {matched} alternate-art {'card' if matched == 1 else 'cards'} to installed printings."
                )
            if missing:
                ids = ", ".join(sorted(missing)[:4])
                if len(missing) > 4:
                    ids += ", …"
                notes.append(
                    f"EDOPro is missing {len(missing)} card ID(s) ({ids}). Update its card databases before playing."
                )
            elif not local and saved:
                notes.append("Open EDOPro to download its card databases before playing.")
            if goat and saved:
                notes.append(
                    f"Saved GOAT versions with {goat_changed} card {'change' if goat_changed == 1 else 'changes'}."
                )
                if goat_review:
                    notes.append(f"Review {len(goat_review)} retained card ID(s).")
                notes.append(GOAT_ROOM_SETTINGS)
            return saved, error, " ".join(notes), tuple(sorted(goat_receipt))

        def failed(message):
            self.importing = False
            self._update_buttons()
            self._message(message, error=True)

        self._submit("import", import_selected, self._import_done, failed)

    def _import_done(self, result):
        saved, error, note, self.goat_receipt = result
        if self.goat_receipt:
            self.goat_changes.grid(row=0, column=2, sticky="e")
        self.importing = False
        self.saved.update(deck.id for deck, _path, _added in saved)
        for deck in self.decks.values():
            self.deck_list.item(str(deck.id), values=self._deck_row(deck))
        self._update_buttons()
        count = sum(1 for _, _, added in saved if added)
        existing = len(saved) - count
        if saved and self.on_sync_artwork:
            self.import_paths = tuple(path for _, path, _ in saved)
            self.sync_button.configure(
                text=f"Sync artwork for {len(saved)} decks"
                if len(saved) > 1
                else "Sync deck artwork"
            )
            self.sync_button.grid(row=0, column=1, padx=(0, 8))
        if count:
            self.master.event_generate("<<TournamentDeckSaved>>", when="tail")
        if len(saved) == 1 and not error:
            _, path, added = saved[0]
            msg = f"{'Added' if added else 'Already in your deck folder'} · {path.name}"
        else:
            msg = f"{count} added · {existing} already in your deck folder"
        if self.cancel_import.is_set():
            msg = f"Import stopped. {count} added · {existing} already in your deck folder"
        if note and self.cancel_import.is_set():
            msg += ". " + note
        elif note:
            msg = f"{count} added · {existing} already saved. {note}"
        self._message(msg + (f". {error}" if error else ""), error=bool(error), success=not error)

    def _clear_sync(self):
        self.import_paths = ()
        self.sync_button.grid_remove()

    def _sync_artwork(self):
        paths = self.import_paths
        if not paths or not self.on_sync_artwork or self.importing:
            return
        folder = (self.game_path / "deck").resolve()
        if not folder.is_relative_to(self.game_path) or any(
            path.is_symlink()
            or not path.is_file()
            or path.suffix.lower() != ".ydk"
            or path.resolve().parent != folder
            for path in paths
        ):
            self._clear_sync()
            self._message(
                "The imported deck files have moved. Add the desired decks again to sync their artwork.",
                error=True,
            )
            return
        if self.on_sync_artwork(self.game_path, paths) is not False:
            self.destroy()

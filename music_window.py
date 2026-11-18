"""Native soundtrack gallery for the deck editor and ordinary duels."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import music_library as library
from config import APP_NAME
from music.view import LibraryView, SeekState, run_job
from music_import import ImportItem, execute_import, import_library, import_power_of_chaos
from music_player import PreviewPlayer

BG = "#151615"
PANEL = "#232521"
RAISED = "#2c302b"
TEXT = "#f7f4ec"
MUTED = "#bdc1b6"
ACCENT = "#8ae8d5"
LINE = "#42463e"


def _time(value: float) -> str:
    minutes, seconds = divmod(int(max(value, 0)), 60)
    return f"{minutes}:{seconds:02}"


class MusicPanel(ttk.Frame):
    def __init__(self, parent, edopro_path: str | Path, scene: str = "deck"):
        super().__init__(parent, padding=(18, 14))
        self.game_path = Path(edopro_path)
        self.view = LibraryView(self.game_path)
        self.games = {}
        self.restorable = {}
        self.scan_request = 0
        self.enabled = None
        self.group = ()
        self.player = PreviewPlayer()
        self.preview_worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="soundtrack-preview"
        )
        self.preview_request = 0
        self.scene = tk.StringVar(self, value=scene if scene in library.SCENES else "deck")
        self.query = tk.StringVar(self)
        self.available = tk.BooleanVar(self)
        self.status = tk.StringVar(self)
        self.current_game = ""
        self.pictures = {}
        self.tiles = {}
        self.events = queue.Queue()
        self.busy = False
        self.scrub = SeekState()
        self._timer = None
        self._build()
        self.refresh()
        self._tick()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self)
        header.grid(sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Soundtrack library", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        scenes = ttk.Frame(header)
        scenes.grid(row=0, column=1, sticky="e")
        for col, (key, name) in enumerate((key, library.SCENES[key]) for key in ("deck", "duel")):
            ttk.Radiobutton(
                scenes,
                text=name,
                value=key,
                variable=self.scene,
                style="Scope.TRadiobutton",
                command=self._switch_scene,
            ).grid(row=0, column=col)
        controls = ttk.Frame(self)
        controls.grid(sticky="ew", pady=(8, 10))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Find a game").grid(row=0, column=0, padx=(0, 8))
        style = ttk.Style(self)
        style.configure(
            "Music.TEntry",
            fieldbackground=RAISED,
            foreground=TEXT,
            insertcolor=TEXT,
            padding=(7, 4),
        )
        style.map("Music.TEntry", bordercolor=[("focus", ACCENT)])
        style.configure(
            "Music.TButton", padding=(8, 4), background=RAISED, foreground=TEXT, borderwidth=1
        )
        style.map(
            "Music.TButton",
            background=[
                ("disabled", PANEL),
                ("pressed", "#28453b"),
                ("active", "#384139"),
                ("focus", RAISED),
            ],
            foreground=[
                ("disabled", "#989e93"),
                ("pressed", TEXT),
                ("active", TEXT),
                ("focus", TEXT),
            ],
        )
        style.configure(
            "Music.Horizontal.TScale",
            background=ACCENT,
            troughcolor=RAISED,
            bordercolor=LINE,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            sliderlength=14,
            sliderthickness=12,
            gripcount=0,
        )
        style.map("Music.Horizontal.TScale", background=[("disabled", LINE), ("active", "#aaf6de")])
        style.configure(
            "Music.Vertical.TScrollbar",
            background=RAISED,
            troughcolor=PANEL,
            bordercolor=LINE,
            arrowcolor=MUTED,
            lightcolor=LINE,
            darkcolor=LINE,
            gripcount=0,
            arrowsize=11,
        )
        style.map(
            "Music.Vertical.TScrollbar", background=[("pressed", "#40594b"), ("active", "#455348")]
        )
        self.search = ttk.Entry(controls, textvariable=self.query, style="Music.TEntry")
        self.search.grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(
            controls,
            text="With audio only",
            variable=self.available,
            command=self._filter,
            style="Gallery.TCheckbutton",
        ).grid(row=0, column=2, padx=(12, 0))
        self.query.trace_add("write", lambda *_: self._filter())
        self.search.bind("<Escape>", lambda _: self.query.set(""))

        body = ttk.Frame(self)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=4)
        body.rowconfigure(0, weight=1)
        gallery = ttk.Frame(body)
        gallery.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        gallery.columnconfigure(0, weight=1)
        gallery.rowconfigure(0, weight=1)
        self.scroll = tk.Canvas(
            gallery, background=BG, borderwidth=0, highlightthickness=0, width=350, height=275
        )
        self.scroll.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(
            gallery, orient="vertical", command=self.scroll.yview, style="Music.Vertical.TScrollbar"
        )
        self.scroll.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")
        self.wall = tk.Frame(self.scroll, bg=BG)
        window = self.scroll.create_window((0, 0), window=self.wall, anchor="nw")
        self.wall.bind(
            "<Configure>", lambda _: self.scroll.configure(scrollregion=self.scroll.bbox("all"))
        )
        self.scroll.bind("<Configure>", lambda e: self.scroll.itemconfigure(window, width=e.width))
        self._wheel_bind(self.scroll)
        self._wheel_bind(self.wall)
        self.detail = tk.Frame(body, bg=PANEL, padx=14, pady=12, width=280)
        self.detail.grid(row=0, column=1, sticky="nsew")
        self.detail.columnconfigure(0, weight=1)
        self.detail.rowconfigure(2, weight=1)
        album = tk.Frame(self.detail, bg=PANEL)
        album.grid(sticky="ew", pady=(0, 12))
        album.columnconfigure(1, weight=1)
        self.art = tk.Label(album, bg=PANEL, anchor="w")
        self.art.grid(row=0, column=0, sticky="w", padx=(0, 12))
        identity = tk.Frame(album, bg=PANEL)
        identity.grid(row=0, column=1, sticky="ew")
        identity.columnconfigure(0, weight=1)
        self.name = tk.Label(
            identity,
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 14, "bold"),
            anchor="w",
            justify="left",
            wraplength=158,
        )
        self.name.grid(sticky="ew", pady=(5, 0))
        self.meta = tk.Label(
            identity, bg=PANEL, fg=MUTED, anchor="w", justify="left", wraplength=158
        )
        self.meta.grid(sticky="ew", pady=(1, 5))
        self.credit = ttk.Button(
            identity, text="LaunchBox cover ↗", command=self._cover, style="Music.TButton"
        )
        self.credit.grid(sticky="w", pady=(3, 0))
        self.audio_label = tk.Label(self.detail, bg=PANEL, fg=ACCENT, anchor="w")
        self.audio_label.grid(sticky="ew")
        list_box = tk.Frame(self.detail, bg=PANEL)
        list_box.grid(sticky="nsew", pady=(2, 6))
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)
        self.tracklist = tk.Listbox(
            list_box,
            bg=RAISED,
            fg=TEXT,
            selectbackground="#36574d",
            selectforeground=TEXT,
            height=3,
            width=27,
            relief="flat",
            activestyle="none",
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=ACCENT,
            exportselection=False,
        )
        self.tracklist.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            list_box, command=self.tracklist.yview, style="Music.Vertical.TScrollbar"
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.tracklist.configure(yscrollcommand=scroll.set)
        self.tracklist.bind("<<ListboxSelect>>", lambda _: self._track_changed())
        self.tracklist.bind("<Double-1>", lambda _: self.toggle_play())
        self.tracklist.bind("<Return>", lambda _: self.toggle_play())
        preview = tk.Frame(self.detail, bg=PANEL)
        preview.grid(sticky="ew")
        preview.columnconfigure(1, weight=1)
        self.play = ttk.Button(
            preview, text="▶  Play", command=self.toggle_play, width=8, style="Music.TButton"
        )
        self.play.grid(row=0, column=0, sticky="w")
        self.seek = ttk.Scale(preview, from_=0, to=100, style="Music.Horizontal.TScale")
        self.seek.grid(row=0, column=1, sticky="ew", padx=8)
        self.seek.bind("<ButtonPress-1>", self._scrub)
        self.seek.bind("<B1-Motion>", self._scrub)
        self.seek.bind("<ButtonRelease-1>", self._finish_scrub)
        for key in ("Left", "Right", "Up", "Down", "Home", "End"):
            self.seek.bind(f"<KeyPress-{key}>", self._keyboard_scrub)
            self.seek.bind(f"<KeyRelease-{key}>", self._finish_scrub)
        self.clock = tk.Label(preview, text="0:00", bg=PANEL, fg=MUTED)
        self.clock.grid(row=0, column=2, sticky="e")
        bottom = tk.Frame(self.detail, bg=PANEL)
        bottom.grid(sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        self.apply = ttk.Button(bottom, style="Aqua.TButton", command=self._apply)
        self.apply.grid(row=0, column=0, sticky="ew")
        self.add = ttk.Button(bottom, text="Add audio…", command=self._add, style="Music.TButton")
        self.add.grid(row=0, column=1, padx=(6, 0))
        links = ttk.Frame(self)
        links.grid(sticky="ew", pady=(10, 0))
        links.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(
            links, textvariable=self.status, style="Status.TLabel", wraplength=375
        )
        self.status_label.grid(row=0, column=0, sticky="w")
        self.enable = ttk.Button(
            links,
            text="Enable game music",
            command=lambda: self._music_setting(True),
            style="Music.TButton",
        )
        self.enable.grid(row=0, column=1, padx=(4, 0))
        self.manage = ttk.Menubutton(links, text="Manage library", style="Music.TMenubutton")
        style.configure(
            "Music.TMenubutton",
            background=RAISED,
            foreground=TEXT,
            padding=(8, 4),
            arrowcolor=ACCENT,
        )
        style.map(
            "Music.TMenubutton", background=[("active", "#384139")], foreground=[("active", TEXT)]
        )
        self.menu = tk.Menu(
            self.manage,
            tearoff=False,
            background=RAISED,
            foreground=TEXT,
            activebackground="#36574d",
            activeforeground=TEXT,
        )
        self.menu.add_command(label="Import from Power of Chaos…", command=self._import_game)
        self.menu.add_command(label="Import soundtrack folder…", command=self._import_folder)
        self.menu.add_command(label="Open soundtrack folder", command=self._open_folder)
        self.menu.add_separator()
        self.menu.add_command(label="Enable music in EDOPro")
        self.manage.configure(menu=self.menu)
        self.manage.grid(row=0, column=2, padx=6)
        self.restore = ttk.Button(
            links, text="Restore playlist", command=self._restore, style="Music.TButton"
        )
        self.restore.grid(row=0, column=3)

    def _wheel_bind(self, widget):
        def scroll(event):
            if getattr(event, "num", None) in (4, 5):
                delta = -1 if event.num == 4 else 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.scroll.yview_scroll(delta, "units")
            return "break"

        for name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(name, scroll)

    def _picture(self, key: str, size: str):
        if (key, size) not in self.pictures:
            try:
                self.pictures[key, size] = tk.PhotoImage(
                    master=self, file=str(library.ART / size / f"{key}.png")
                )
            except tk.TclError:
                self.pictures[key, size] = None
        return self.pictures[key, size]

    def _switch_scene(self):
        self.stop_preview()
        self.status.set("")
        self._show_scene()

    def reveal_game(self, key: str):
        if not self.games:
            self.current_game = key
            return
        if key not in self.games:
            return
        self.available.set(False)
        self.query.set("")
        self._select(key)
        if key in self.tiles:
            self.after_idle(self._reveal, self.tiles[key])

    def refresh(self):
        self.scan_request += 1
        request = self.scan_request

        def scan():
            view = LibraryView(self.game_path)
            view.refresh()
            flags = {key: library.can_restore(self.game_path, key) for key in library.SCENES}
            return view, flags, library.music_enabled(self.game_path)

        def worker():
            self.events.put(("scan", (request, run_job(scan))))

        threading.Thread(target=worker, name="soundtrack-scan", daemon=True).start()

    def _show_scene(self):
        scene = self.scene.get()
        enabled = self.enabled
        self.menu.entryconfigure(
            "end",
            label="Disable music in EDOPro" if enabled else "Enable music in EDOPro",
            command=lambda: self._music_setting(not enabled),
            state="normal" if enabled is not None else "disabled",
        )
        if enabled is False:
            self.enable.state(["disabled"] if self.busy else ["!disabled"])
            self.enable.grid()
        else:
            self.enable.grid_remove()
        self.restore.state(
            ["!disabled"] if self.restorable.get(scene) and not self.busy else ["disabled"]
        )
        self._filter(preserve_scroll=True)
        if not self.status.get():
            if enabled is False:
                self.status.set(
                    "Music is off in this EDOPro installation. Preview is still available."
                )
            else:
                self.status.set("Play a track to preview it. Apply it to change this playlist.")

    def _filter(self, *, preserve_scroll=False):
        if not hasattr(self, "wall"):
            return
        top = self.scroll.yview()[0] if preserve_scroll else 0
        selection = self.view.query(
            self.scene.get(),
            self.query.get(),
            audio_only=self.available.get(),
            game=self.current_game,
        )
        games = [album.game for album in selection.games]
        counts = {album.game.id: album.counts[self.scene.get()] for album in selection.games}
        self.games.update((g.id, g) for g in games)
        for w in self.wall.winfo_children():
            w.destroy()
        self.tiles.clear()
        # Two broad cards remain readable on a Steam Deck and at Windows display scaling.
        for c in range(2):
            self.wall.columnconfigure(c, weight=1, uniform="albums")
        for index, game in enumerate(games):
            count = counts[game.id]
            tile = tk.Frame(
                self.wall,
                bg=RAISED,
                highlightthickness=2,
                highlightbackground=LINE,
                highlightcolor=ACCENT,
                takefocus=True,
                cursor="hand2",
            )
            tile.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, 6), pady=(0, 6))
            tile.columnconfigure(0, weight=1)
            if game.cover_page and (picture := self._picture(game.id, "grid")):
                cover = tk.Label(tile, image=picture, bg=RAISED, height=140)
            else:
                cover = tk.Label(
                    tile, text="♫", font=("TkDefaultFont", 28), fg=ACCENT, bg=RAISED, height=2
                )
            cover.grid(sticky="ew", pady=(4, 0))
            title = tk.Label(
                tile,
                text=game.title,
                bg=RAISED,
                fg=TEXT,
                anchor="w",
                justify="left",
                wraplength=155,
            )
            title.grid(sticky="ew", padx=7, pady=(3, 0))
            secondary = (f"{game.year}  ·  " if game.year else "") + (
                f"{count} track" + ("s" if count != 1 else "") if count else "Add audio"
            )
            meta = tk.Label(
                tile, text=secondary, fg=ACCENT if count else MUTED, bg=RAISED, anchor="w"
            )
            meta.grid(sticky="ew", padx=7, pady=(0, 5))
            for widget in (tile, cover, title, meta):
                widget.bind("<Button-1>", lambda _, key=game.id: self._select(key))
                self._wheel_bind(widget)
            tile.bind("<Return>", lambda _, key=game.id: self._select(key))
            tile.bind("<space>", lambda _, key=game.id: self._select(key))
            tile.bind("<FocusIn>", lambda _, w=tile: self._reveal(w))
            self.tiles[game.id] = tile
        if not games:
            tk.Label(self.wall, text="No matching games", bg=BG, fg=MUTED, pady=20).grid()
        key = selection.game or ""
        self._select(key, reset=True)
        self.scroll.yview_moveto(top)

    def _reveal(self, widget):
        if not widget.winfo_exists():
            return
        self.update_idletasks()
        if not widget.winfo_exists():
            return
        total = max(1, self.wall.winfo_height())
        top, bottom = self.scroll.yview()
        y = widget.winfo_y()
        if y / total < top or (y + widget.winfo_height()) / total > bottom:
            self.scroll.yview_moveto(y / total)

    def _select(self, key, *, reset=False):
        if not reset and key == self.current_game:
            return
        old_track = self._track() if key == self.current_game else None
        self.stop_preview()
        self.current_game = key
        for item, tile in self.tiles.items():
            tile.configure(highlightbackground=ACCENT if item == key else LINE)
        game = self.games.get(key)
        self.name.configure(text=game.title if game else "No game selected")
        edition = f"{game.platform}  ·  {game.year}" if game and game.year else ""
        if game and game.region:
            edition += f"\n{game.region}"
        self.meta.configure(text=edition)
        if game and game.cover_page:
            self.credit.configure(
                text="LaunchBox fan cover ↗" if game.fan_cover else "LaunchBox cover ↗"
            )
            self.credit.grid()
        else:
            self.credit.grid_remove()
        size = "detail" if self.winfo_screenheight() >= 720 else "grid"
        if game and game.cover_page and (picture := self._picture(key, size)):
            self.art.configure(image=picture, text="")
        else:
            self.art.configure(
                image="", text="♫" if key else "", font=("TkDefaultFont", 22), fg=ACCENT
            )
        self.tracklist.delete(0, "end")
        selection = self.view.query(
            self.scene.get(),
            self.query.get(),
            game=key,
            track=old_track.path if old_track else None,
        )
        self.group = group = selection.tracks if key else ()
        self.audio_label.configure(
            text=f"{library.SCENES[self.scene.get()]}  ·  {len(group)} available"
        )
        for track in group:
            self.tracklist.insert(
                "end",
                f"{track.title}   ·   {track.path.suffix[1:].upper()}"
                + ("   ✓" if track.in_playlist else ""),
            )
        if group:
            index = group.index(selection.track)
            self.tracklist.selection_set(index)
            self.tracklist.activate(index)
        elif game:
            self.tracklist.insert("end", "Add your audio files to start.")
        self.add.state(["!disabled"] if key and not self.busy else ["disabled"])
        self._track_changed()

    def _track(self):
        i = self.tracklist.curselection()
        group = self.group
        return group[i[0]] if i and i[0] < len(group) else None

    def _track_changed(self):
        self.stop_preview()
        track = self._track()
        state = ["!disabled"] if track and not self.busy else ["disabled"]
        self.play.state(state)
        self.apply.state(state)
        self.apply.configure(text=f"Use for {library.SCENES[self.scene.get()].lower()}")
        self.seek.state(["disabled"])

    def toggle_play(self):
        track = self._track()
        if not track or self.busy:
            return

        def toggle() -> None:
            if self.player.playing:
                self.player.pause()
            else:
                self.player.play(track.path, seconds=self.player.resume_position(track.path))

        self._preview(toggle)

    def _preview(self, action: Callable[[], None]) -> Future:
        self.preview_request += 1
        request = self.preview_request

        def worker() -> None:
            outcome = run_job(action)
            self.events.put(("preview", (request, outcome.error)))

        return self.preview_worker.submit(worker)

    def stop_preview(self) -> Future:
        stopped = self._preview(self.player.stop)
        self.scrub.fraction = None
        if hasattr(self, "play"):
            self.play.configure(text="▶  Play")
            self.clock.configure(text="0:00")
            self.seek.set(0)
        return stopped

    def _scrub(self, event):
        if self.player.seekable:
            self.seek.focus_set()
            self.scrub.drag(self.seek.get(event.x, event.y) / 100)
            self.seek.set(self.scrub.fraction * 100)
            self._player_ui()
        return "break"

    def _keyboard_scrub(self, event):
        if self.player.seekable and self.player.duration:
            self.scrub.keyboard(event.keysym, self.player.position, self.player.duration)
            self.seek.set(self.scrub.fraction * 100)
            self._player_ui()
        return "break"

    def _finish_scrub(self, event):
        if self.scrub.fraction is None:
            return "break"
        if event.type == tk.EventType.ButtonRelease:
            self.scrub.drag(self.seek.get(event.x, event.y) / 100)
        offset = self.scrub.release(self.player.duration)
        if offset is not None:
            self._preview(lambda: self.player.seek(offset))
        self._player_ui()
        return "break"

    def _player_ui(self):
        self.play.configure(text="Ⅱ  Pause" if self.player.playing else "▶  Play")
        self.seek.state(["!disabled"] if self.player.seekable else ["disabled"])
        if self.player.path:
            if self.scrub.fraction is not None:
                self.scrub.drag(self.seek.get() / 100)
            position = self.scrub.position(self.player.position, self.player.duration)
            self.clock.configure(
                text=f"{_time(position)} / {_time(self.player.duration)}"
                if self.player.duration
                else _time(self.player.position)
            )
            if self.player.duration and self.scrub.fraction is None:
                self.seek.set(100 * self.player.position / self.player.duration)

    def _work(self, action, result: str):
        if self.busy:
            return
        stopped = self.stop_preview()
        self.busy = True
        self.add.state(["disabled"])
        self.apply.state(["disabled"])
        self.play.state(["disabled"])
        self.restore.state(["disabled"])
        self.manage.state(["disabled"])
        self.enable.state(["disabled"])
        self.status.set("Updating soundtrack files…")

        def update():
            stopped.result()
            return action()

        def worker():
            outcome = run_job(update)
            message = outcome.error or outcome.value or result
            self.events.put(("done", message))

        threading.Thread(target=worker, name="soundtrack-library", daemon=False).start()

    def _add(self):
        key, scene = self.current_game, self.scene.get()
        if not key:
            return
        names = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(),
            title=f"Add music to {self.games[key].title}",
            filetypes=[("Audio files", "*.flac *.ogg *.mp3 *.wav"), ("All files", "*")],
        )
        if names:

            def add():
                rows = execute_import(
                    (ImportItem(Path(name), key, scene) for name in names), dedup_library=False
                )
                counts = {
                    s: sum(r.status == s for r in rows)
                    for s in ("imported", "duplicate", "unsupported", "failed")
                }
                return (
                    ", ".join(f"{n} {s}" for s, n in counts.items())
                    + ". "
                    + " ".join(f"{r.path.name}: {r.reason}" for r in rows if r.reason)
                )

            self._work(add, f"Added audio to {self.games[key].title}.")

    def _bulk_import(self, fn, path):
        def copy():
            def progress(done, total):
                self.events.put(("progress", f"Importing recording {done} of {total}…"))

            added, found = fn(Path(path), progress=progress)
            return f"Imported {added} recording{'s' if added != 1 else ''}. Already in your library: {found}."

        self._work(copy, "Soundtrack import complete.")

    def _import_folder(self):
        folder = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title="Import a soundtracks folder copied from another installation",
            mustexist=True,
        )
        if folder:
            self._bulk_import(import_library, folder)

    def _import_game(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Choose an original Power of Chaos Voice.dat or game ZIP",
            filetypes=[("Power of Chaos audio", "*.dat *.DAT *.zip *.ZIP"), ("All files", "*")],
        )
        if path:
            self._bulk_import(import_power_of_chaos, path)

    def _open_folder(self):
        folder = library.library_root()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
            self.status.set(
                "To transfer your collection, copy this soundtracks folder and import it on the other device."
            )
        except OSError as exc:
            self.status.set(f"Your soundtracks folder is {folder}. {exc}")

    def _apply(self):
        track = self._track()
        if not track or self.busy:
            return
        scope = library.SCENES[track.scene].lower()
        if not messagebox.askokcancel(
            "Use this soundtrack?",
            f"Use “{track.title}” as the only track for {scope}?\n\nRestore playlist brings back your current music. Close and reopen EDOPro to hear changes.",
            parent=self.winfo_toplevel(),
            default="ok",
        ):
            return
        hint = " Enable Music in EDOPro’s audio settings." if self.enabled is False else ""
        self._work(
            lambda: library.use_track(self.game_path, track),
            f"Selected {track.title} for {scope}. Restart EDOPro.{hint}",
        )

    def _restore(self):
        scene = self.scene.get()
        self._work(
            lambda: library.restore_playlist(self.game_path, scene),
            f"Restored the {library.SCENES[scene].lower()} playlist. Restart EDOPro to hear it.",
        )

    def _music_setting(self, enabled: bool):
        if self.busy:
            return
        self._work(
            lambda: library.set_music_enabled(self.game_path, enabled),
            "EDOPro music enabled at your existing volume."
            if enabled
            else "EDOPro music disabled.",
        )

    def _cover(self):
        game = self.games.get(self.current_game)
        if game and game.cover_page:
            webbrowser.open(game.cover_page)

    def _tick(self):
        while True:
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "preview":
                request, error = message
                if request == self.preview_request:
                    if error:
                        self.status.set(error)
                    self._player_ui()
                continue
            if kind == "scan":
                request, message = message
                if request != self.scan_request:
                    continue
                if message.error:
                    self.status.set(message.error)
                else:
                    self.view, self.restorable, self.enabled = message.value
                    self._show_scene()
                continue
            self.status.set(message)
            if kind == "done":
                self.busy = False
                self.manage.state(["!disabled"])
                self.enable.state(["!disabled"])
                self.refresh()
        if self.player.path:
            self._player_ui()
        self._timer = self.after(180, self._tick)

    def _on_destroy(self, event):
        if event.widget is self:
            if self._timer is not None:
                self.after_cancel(self._timer)
                self._timer = None
            self.preview_worker.submit(self.player.stop)
            self.preview_worker.shutdown(wait=False)


def open_music_library(
    parent, game_path: str | Path, scene: str = "deck", *, game: str | None = None
):
    """Open one gallery per parent; refresh it if the installation changes."""
    existing = getattr(parent, "_music_library_window", None)
    if existing is not None and existing.winfo_exists():
        if existing.panel.game_path.resolve() == Path(game_path).resolve():
            if game:
                existing.panel.reveal_game(game)
            existing.lift()
            existing.focus_set()
            return existing
        existing.destroy()
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title(f"Soundtrack library · {APP_NAME}")
    window.transient(parent.winfo_toplevel())
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    panel = MusicPanel(window, game_path, scene)
    panel.grid(sticky="nsew")
    window.panel = panel
    parent._music_library_window = window
    width = min(960, window.winfo_screenwidth() - 32)
    height = min(668, window.winfo_screenheight() - 92)
    window.minsize(min(700, width), min(520, height))
    host = parent.winfo_toplevel()
    x = max(
        0,
        min(
            host.winfo_rootx() + (host.winfo_width() - width) // 2,
            window.winfo_screenwidth() - width,
        ),
    )
    y = max(
        0,
        min(
            host.winfo_rooty() + (host.winfo_height() - height) // 2,
            window.winfo_screenheight() - height,
        ),
    )
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.deiconify()
    if sys.platform == "win32":
        from gui import _apply_windows_dark_titlebar

        _apply_windows_dark_titlebar(window)
    if game:
        panel.reveal_game(game)
    elif panel.current_game in panel.tiles:
        panel.after_idle(panel._reveal, panel.tiles[panel.current_game])
    panel.search.focus_set()
    return window

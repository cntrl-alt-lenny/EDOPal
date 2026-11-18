"""Native soundtrack gallery for the deck editor and ordinary duels."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import music_library as library
from music_import import import_library, import_power_of_chaos
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
        self.games = library.catalog()
        self.player = PreviewPlayer()
        self.scene = tk.StringVar(self, value=scene if scene in library.SCENES else "deck")
        self.query = tk.StringVar(self)
        self.available = tk.BooleanVar(self)
        self.status = tk.StringVar(self)
        self.current_game = ""
        self.items: list[library.Track] = []
        self.by_game: dict[str, list[library.Track]] = {}
        self.pictures = {}
        self.tiles = {}
        self.events = queue.Queue()
        self.busy = False
        self._dragging = False
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
        for col, (key, name) in enumerate(library.SCENES.items()):
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
            self.pictures[key, size] = tk.PhotoImage(
                master=self, file=str(library.ART / size / f"{key}.png")
            )
        return self.pictures[key, size]

    def _switch_scene(self):
        self.stop_preview()
        self.status.set("")
        self.refresh()

    def reveal_game(self, key: str):
        if key not in self.games:
            return
        self.available.set(False)
        self.query.set("")
        self._select(key)
        if key in self.tiles:
            self.after_idle(self._reveal, self.tiles[key])

    def refresh(self):
        scene = self.scene.get()
        enabled = library.music_enabled(self.game_path)
        self.menu.entryconfigure(
            "end",
            label="Disable music in EDOPro" if enabled else "Enable music in EDOPro",
            command=lambda: self._music_setting(not enabled),
            state="normal" if enabled is not None else "disabled",
        )
        if enabled is False:
            self.enable.grid()
        else:
            self.enable.grid_remove()
        try:
            self.items = library.tracks(self.game_path, scene)
            restorable = library.can_restore(self.game_path, scene)
        except (OSError, ValueError) as exc:
            self.items = []
            self.status.set(str(exc))
            restorable = False
        self.restore.state(["!disabled"] if restorable else ["disabled"])
        self.by_game = {}
        for track in self.items:
            self.by_game.setdefault(track.game, []).append(track)
        for group in self.by_game.values():
            group.sort(key=lambda t: (not t.in_playlist, t.title.casefold()))
        if not self.current_game:
            self.current_game = next((entry.game for entry in self.items if entry.in_playlist), "")
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
        query = self.query.get().strip().casefold()
        games = [
            g
            for g in self.games.values()
            if (not self.available.get() or g.id in self.by_game)
            and (g.id != library.LOCAL or g.id in self.by_game)
            and (
                not query
                or query in " ".join((g.title, g.platform, str(g.year), *g.aliases)).casefold()
            )
        ]
        games.sort(key=lambda g: (g.id not in self.by_game, g.id == library.LOCAL, g.year, g.title))
        for w in self.wall.winfo_children():
            w.destroy()
        self.tiles.clear()
        # Two broad cards remain readable on a Steam Deck and at Windows display scaling.
        for c in range(2):
            self.wall.columnconfigure(c, weight=1, uniform="albums")
        for index, game in enumerate(games):
            count = len(self.by_game.get(game.id, ()))
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
            if game.id != library.LOCAL:
                cover = tk.Label(tile, image=self._picture(game.id, "grid"), bg=RAISED, height=140)
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
        key = self.current_game if self.current_game in self.tiles else games[0].id if games else ""
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
        if game and key != library.LOCAL:
            self.art.configure(
                image=self._picture(key, "detail" if self.winfo_screenheight() >= 720 else "grid"),
                text="",
            )
        else:
            self.art.configure(
                image="", text="♫" if key else "", font=("TkDefaultFont", 22), fg=ACCENT
            )
        self.tracklist.delete(0, "end")
        group = self.by_game.get(key, [])
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
            self.tracklist.selection_set(0)
            self.tracklist.activate(0)
        elif game:
            self.tracklist.insert("end", "Add your audio files to start.")
        self.add.state(["!disabled"] if key and not self.busy else ["disabled"])
        self._track_changed()

    def _track(self):
        i = self.tracklist.curselection()
        group = self.by_game.get(self.current_game, [])
        return group[i[0]] if i and i[0] < len(group) else None

    def _track_changed(self):
        self.stop_preview()
        track = self._track()
        state = ["!disabled"] if track and not self.busy else ["disabled"]
        self.play.state(state)
        self.apply.state(state)
        self.apply.configure(
            text="Use for deck editor" if self.scene.get() == "deck" else "Use for duels"
        )
        self.seek.state(["disabled"])

    def toggle_play(self):
        track = self._track()
        if not track or self.busy:
            return
        if self.player.playing:
            self.player.pause()
        else:
            start = self.player.position if self.player.path == track.path.resolve() else 0
            if self.player.duration and start >= self.player.duration - 0.1:
                start = 0
            try:
                self.player.play(track.path, seconds=start)
            except (ValueError, OSError) as exc:
                self.status.set(str(exc))
                return
        self._player_ui()

    def stop_preview(self):
        self.player.stop()
        self._dragging = False
        if hasattr(self, "play"):
            self.play.configure(text="▶  Play")
            self.clock.configure(text="0:00")
            self.seek.set(0)

    def _scrub(self, event):
        if self.player.seekable:
            self.seek.focus_set()
            self._dragging = True
            self.seek.set(self.seek.get(event.x, event.y))
            self._player_ui()
        return "break"

    def _keyboard_scrub(self, event):
        if self.player.seekable and self.player.duration:
            self._dragging = True
            if event.keysym in ("Home", "End"):
                value = 0 if event.keysym == "Home" else 100
            else:
                step = 500 / self.player.duration  # Five seconds, independent of track length.
                value = self.seek.get() + (step if event.keysym in ("Right", "Up") else -step)
            self.seek.set(value)
            self._player_ui()
        return "break"

    def _finish_scrub(self, event):
        if not self._dragging:
            return "break"
        if event.type == tk.EventType.ButtonRelease:
            self.seek.set(self.seek.get(event.x, event.y))
        self._dragging = False
        try:
            self.player.seek(self.seek.get() * self.player.duration / 100)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
        self._player_ui()
        return "break"

    def _player_ui(self):
        self.play.configure(text="Ⅱ  Pause" if self.player.playing else "▶  Play")
        self.seek.state(["!disabled"] if self.player.seekable else ["disabled"])
        if self.player.path:
            position = (
                self.seek.get() * self.player.duration / 100
                if self._dragging
                else self.player.position
            )
            self.clock.configure(
                text=f"{_time(position)} / {_time(self.player.duration)}"
                if self.player.duration
                else _time(self.player.position)
            )
            if self.player.duration and not self._dragging:
                self.seek.set(100 * self.player.position / self.player.duration)

    def _work(self, action, result: str):
        if self.busy:
            return
        self.stop_preview()
        self.busy = True
        self.add.state(["disabled"])
        self.apply.state(["disabled"])
        self.play.state(["disabled"])
        self.restore.state(["disabled"])
        self.manage.state(["disabled"])
        self.enable.state(["disabled"])
        self.status.set("Updating soundtrack files…")

        def worker():
            try:
                message = action() or result
            except (ValueError, OSError) as exc:
                message = str(exc)
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
                for name in names:
                    library.import_track(Path(name), key, scene)

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
        hint = (
            " Enable Music in EDOPro’s audio settings."
            if library.music_disabled(self.game_path)
            else ""
        )
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
            self.player.stop()


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
    window.title("Soundtrack library · EDOPro HD Sync")
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

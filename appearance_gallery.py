"""Native previews and scoped controls for the Customize EDOPro gallery."""

import contextlib
import struct
import tkinter as tk
import zlib
from pathlib import Path
from tkinter import ttk
from zipfile import ZipFile

import extras

ART = Path(__file__).resolve().parent / "assets" / "window" / "gallery"
BACKGROUND = "#111b21"
SURFACE = "#19272e"
HOVER = "#263b42"
LINE = "#35494f"
INK = "#eff4ef"
MUTED = "#adbfbe"
MINT = "#96e7d6"
TABS = ("Game skins", "Card sleeves", "Duel effects", "Music")
VIEWS = {"Menu": "menu", "Deck editor": "deck", "Duel field": "duel"}
UNDO_GROUPS = {
    "Card sleeves": ("sleeves", "Undo sleeve change"),
    "Duel effects": ("effects", "Undo effects change"),
    "Music": ("music", "Undo synth pair"),
}
SKIN_DETAILS = {
    "Abyss": "Kyogre in deep ocean blue, with cool teal highlights on the field.",
    "Moon": "A close lunar portrait and charcoal tones frame the menus and playmat.",
    "Nebula": "Blue starlight and a violet field give the deck editor a night-sky palette.",
    "Ristretto": "Armoured figures, warm copper light and ember-coloured details.",
}


def _styles(parent, family):
    style = ttk.Style(parent)
    for prefix, bg in (("Studio", BACKGROUND), ("Panel", SURFACE)):
        style.configure(prefix + ".TFrame", background=bg)
        style.configure(prefix + ".TLabel", background=bg, foreground=INK)
        style.configure(
            prefix + "Title.TLabel", background=bg, foreground=INK, font=(family, 16, "bold")
        )
        style.configure(
            prefix + "Name.TLabel", background=bg, foreground=INK, font=(family, 12, "bold")
        )
        style.configure(prefix + "Hint.TLabel", background=bg, foreground=MUTED, font=(family, 9))
    style.configure(
        "PanelKicker.TLabel", background=SURFACE, foreground=MINT, font=(family, 9, "bold")
    )
    style.configure("StudioGood.TLabel", background=BACKGROUND, foreground=MINT, font=(family, 9))
    style.configure(
        "StudioError.TLabel", background=BACKGROUND, foreground="#ffa9a3", font=(family, 9)
    )
    for prefix, bg, pad in (("Studio", SURFACE, (11, 6)), ("Panel", HOVER, (9, 6))):
        name = prefix + ".TButton"
        style.configure(
            name,
            background=bg,
            foreground=INK,
            padding=pad,
            width=0,
            relief="solid",
            borderwidth=1,
            bordercolor=LINE,
            lightcolor=bg,
            darkcolor=bg,
            anchor="center",
            focuscolor=MINT,
        )
        style.map(
            name,
            background=[("active", HOVER)],
            bordercolor=[("focus", MINT), ("active", MUTED)],
            foreground=[("disabled", MUTED)],
        )
    style.layout("GalleryTab.TRadiobutton", style.layout("Toolbutton"))
    style.configure(
        "GalleryTab.TRadiobutton",
        padding=(12, 6),
        background=BACKGROUND,
        foreground=MUTED,
        font=(family, 10),
        borderwidth=0,
        focuscolor=MINT,
    )
    style.map(
        "GalleryTab.TRadiobutton",
        background=[("selected", HOVER), ("active", SURFACE)],
        foreground=[("selected", MINT), ("active", INK)],
    )
    style.configure(
        "Scene.TButton",
        padding=(9, 4),
        width=0,
        background=SURFACE,
        foreground=INK,
        borderwidth=1,
        bordercolor=LINE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        relief="solid",
        focuscolor=MINT,
    )
    style.map(
        "Scene.TButton",
        background=[("selected", HOVER), ("active", HOVER)],
        bordercolor=[("selected", MINT), ("focus", MINT)],
        foreground=[("selected", MINT)],
    )
    style.configure(
        "Sleeve.TRadiobutton",
        background=SURFACE,
        foreground=INK,
        padding=(2, 5),
        indicatorbackground=BACKGROUND,
        indicatorforeground=MINT,
        focuscolor=MINT,
    )
    style.map(
        "Sleeve.TRadiobutton",
        background=[("active", HOVER)],
        indicatorforeground=[("selected", MINT)],
    )
    for name, bg in (("PanelTurn.TCheckbutton", SURFACE), ("StudioTurn.TCheckbutton", BACKGROUND)):
        style.configure(
            name,
            background=bg,
            foreground=INK,
            indicatorbackground=BACKGROUND,
            indicatorforeground=MINT,
            focuscolor=MINT,
            padding=(2, 3),
        )
        style.map(name, background=[("active", HOVER)], indicatorforeground=[("selected", MINT)])
    style.configure(
        "SkinTile.TButton",
        width=0,
        background=SURFACE,
        foreground=INK,
        padding=(5, 4),
        relief="flat",
        borderwidth=1,
        bordercolor=LINE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        anchor="center",
        focuscolor=MINT,
    )
    style.map(
        "SkinTile.TButton",
        background=[("selected", HOVER), ("active", HOVER)],
        bordercolor=[("selected", MINT), ("focus", MINT), ("active", MUTED)],
        foreground=[("selected", MINT)],
    )


class AppearanceGallery(ttk.Frame):
    def __init__(self, parent, *, family, back, apply, open_music, on_layout):
        _styles(parent, family)
        self.compact = parent.winfo_screenheight() < 660 or parent.winfo_screenwidth() < 850
        padding = (14, 8, 14, 8) if self.compact else (20, 15, 20, 14)
        super().__init__(parent, style="Studio.TFrame", padding=padding)
        self.columnconfigure(0, weight=1)
        self.family = family
        self.apply = apply
        self.open_music = open_music
        self.on_layout = on_layout
        self.busy = False
        self.game_path = ""
        self.config_path = ""
        self.active_skin = ""
        self.images = {}
        self.player = None
        self.large_preview = None
        self.preview_after = None
        self.panels = {}
        self.controls = []
        self.tabs = {}
        self.tab = tk.StringVar(self, value=TABS[0])
        self.view = tk.StringVar(self, value="Menu")
        self.skin = extras.SKINS[0]
        self.turn_highlight = tk.BooleanVar(self)
        self.installed_highlights = {}
        self.preview_highlights = {}
        self.sleeves = [tk.StringVar(self, value=""), tk.StringVar(self, value="")]
        self.current_sleeves = {}
        self.pack = tk.StringVar(self, value="Thonk")
        self.installed_label = tk.StringVar(self)
        self.skin_title = tk.StringVar(self)
        self.skin_description = tk.StringVar(self)
        self.status = tk.StringVar(self)

        header = ttk.Frame(self, style="Studio.TFrame")
        header.grid(sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Customize EDOPro", style="StudioTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.back_button = self._button(header, "Back to artwork", back, style="Studio.TButton")
        self.back_button.grid(row=0, column=1, sticky="e")
        nav = ttk.Frame(self, style="Studio.TFrame")
        nav.grid(sticky="ew", pady=(10, 0))
        for i, name in enumerate(TABS):
            button = ttk.Radiobutton(
                nav,
                text=name,
                value=name,
                variable=self.tab,
                style="GalleryTab.TRadiobutton",
                command=self.show_tab,
            )
            button.grid(row=0, column=i, padx=(0, 4))
            self.controls.append(button)
            self.tabs[name] = button
            for key, step in (("<Left>", -1), ("<Right>", 1)):
                button.bind(key, lambda _event, offset=step: self._step_tab(offset))
        tk.Frame(self, height=1, background=LINE).grid(sticky="ew", pady=(6, 13))
        self.body = ttk.Frame(self, style="Studio.TFrame")
        self.body.grid(sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build_skins()
        self._build_sleeves()
        self._build_effects()
        self._build_music()

        tk.Frame(self, height=1, background=LINE).grid(sticky="ew", pady=(12, 8))
        footer = ttk.Frame(self, style="Studio.TFrame")
        footer.grid(sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(
            footer, textvariable=self.status, style="StudioHint.TLabel", wraplength=410
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.restore_button = self._button(
            footer,
            "Restore previous skin",
            self._restore_current,
            style="Studio.TButton",
        )
        self.restore_button.grid(row=0, column=1, padx=(0, 8))
        self.apply_button = self._button(footer, "", self._apply_selection, style="Aqua.TButton")
        self.apply_button.grid(row=0, column=2)
        footer.rowconfigure(0, minsize=self.apply_button.winfo_reqheight())
        self.bind("<Destroy>", lambda e: self.stop_preview() if e.widget is self else None)

    def _image(self, key):
        if key not in self.images:
            self.images[key] = tk.PhotoImage(master=self, file=str(ART / (key + ".png")))
        return self.images[key]

    def _button(self, parent, text, command, *, style, **options):
        button = ttk.Button(parent, text=text, command=command, style=style, **options)
        self.controls.append(button)
        return button

    def _panel(self, tab):
        panel = ttk.Frame(self.body, style="Studio.TFrame")
        panel.columnconfigure(0, weight=1)
        self.panels[tab] = panel
        return panel

    def _build_skins(self):
        panel = self._panel("Game skins")
        top = ttk.Frame(panel, style="Studio.TFrame")
        top.grid(sticky="ew", pady=(0, 7))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, textvariable=self.installed_label, style="StudioHint.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.scene_buttons = {}
        for index, view in enumerate(VIEWS, 1):
            button = self._button(
                top, view, lambda name=view: self._set_view(name), style="Scene.TButton"
            )
            button.grid(row=0, column=index, padx=(3, 0))
            self.scene_buttons[view] = button
        hero = ttk.Frame(panel, style="Panel.TFrame")
        hero.grid(sticky="ew")
        hero.columnconfigure(0, weight=1)
        info = ttk.Frame(hero, padding=(18, 12), style="Panel.TFrame")
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(0, weight=1)
        ttk.Label(info, text="MONOKAI", style="PanelKicker.TLabel").grid(sticky="w")
        ttk.Label(
            info,
            textvariable=self.skin_title,
            style="PanelTitle.TLabel",
            font=(self.family, 26, "bold"),
        ).grid(sticky="w", pady=(0, 5))
        ttk.Label(
            info,
            textvariable=self.skin_description,
            style="PanelHint.TLabel",
            wraplength=245,
            justify="left",
        ).grid(sticky="w")
        self.turn_option = ttk.Checkbutton(
            info,
            text="Highlight the turn player",
            variable=self.turn_highlight,
            command=self._toggle_turn_highlight,
            style="PanelTurn.TCheckbutton",
        )
        self.controls.append(self.turn_option)
        self.turn_option.grid(sticky="w", pady=(7, 0))
        self.turn_detail = ttk.Label(info, style="Panel.TLabel")
        self.turn_detail.grid(sticky="w", pady=(2, 0))
        self.enlarge = self._button(info, "Enlarge preview", self._enlarge, style="Panel.TButton")
        self.enlarge.grid(sticky="w", pady=(8, 0))
        self.preview = ttk.Label(hero, style="Panel.TLabel")
        self.preview.grid(row=0, column=1, sticky="e")
        rail = ttk.Frame(panel, style="Studio.TFrame")
        rail.grid(sticky="ew", pady=(7, 0))
        self.tiles = {}
        for index, skin in enumerate(extras.SKINS):
            rail.columnconfigure(index, weight=1, uniform="theme")
            button = self._button(
                rail,
                "  " + skin,
                lambda name=skin: self.select_skin(name),
                style="SkinTile.TButton",
                image=self._image(skin.lower() + "-tile"),
                compound="left",
            )
            button.grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 3, 0 if index == len(extras.SKINS) - 1 else 3),
            )
            self.tiles[skin] = button

    def select_skin(self, skin):
        self.skin = skin
        self.turn_highlight.set(self.preview_highlights.get(skin, False))
        self.skin_title.set(skin)
        self.skin_description.set(SKIN_DETAILS[skin])
        for name, button in self.tiles.items():
            button.state(["selected"] if name == skin else ["!selected"])
        self._set_view(self.view.get())
        self._refresh_actions()

    def _toggle_turn_highlight(self):
        self.preview_highlights[self.skin] = self.turn_highlight.get()
        self._set_view("Duel field")
        self._refresh_actions()
        self.on_layout()

    def _preview_key(self):
        scene = VIEWS[self.view.get()]
        if scene == "duel" and self.turn_highlight.get():
            scene = "turn"
        return f"{self.skin.lower()}-{scene}"

    def _set_view(self, view):
        self.view.set(view)
        key = self._preview_key()
        self.preview.configure(image=self._image(("compact/" if self.compact else "") + key))
        if view == "Duel field":
            self.turn_detail.configure(image=self._image(key + "-lp"))
            self.turn_detail.grid()
        else:
            self.turn_detail.grid_remove()
        for name, button in self.scene_buttons.items():
            button.state(["selected"] if name == view else ["!selected"])
        if self.large_preview is not None:
            self._update_large()

    def _enlarge(self):
        if self.large_preview is not None:
            self.large_preview.lift()
            return
        win = self.large_preview = tk.Toplevel(self, background=BACKGROUND)
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)
        wrap = ttk.Frame(win, style="Studio.TFrame", padding=10)
        wrap.grid()
        self.large_image = ttk.Label(wrap, style="Studio.TLabel")
        self.large_image.grid(sticky="ew")
        bar = ttk.Frame(wrap, style="Studio.TFrame")
        bar.grid(sticky="ew", pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        self.large_caption = ttk.Label(bar, style="StudioName.TLabel")
        self.large_caption.grid(row=0, column=0, sticky="w")
        self.large_buttons = {}
        for index, view in enumerate(VIEWS, 1):
            button = ttk.Button(
                bar,
                text=view,
                command=lambda name=view: self._set_view(name),
                style="Scene.TButton",
            )
            button.grid(row=0, column=index, padx=(0, 3))
            self.large_buttons[view] = button
        close = ttk.Button(bar, text="Close", command=self._close_large, style="Studio.TButton")
        close.grid(row=0, column=4, padx=(6, 0))
        ttk.Checkbutton(
            bar,
            text="Highlight the turn player",
            variable=self.turn_highlight,
            command=self._toggle_turn_highlight,
            style="StudioTurn.TCheckbutton",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(5, 0))
        self._update_large()
        win.protocol("WM_DELETE_WINDOW", self._close_large)
        win.bind("<Escape>", lambda _event: self._close_large())
        for key, delta in (("<Left>", -1), ("<Right>", 1)):
            win.bind(
                key,
                lambda _event, offset=delta: self._set_view(
                    tuple(VIEWS)[(tuple(VIEWS).index(self.view.get()) + offset) % len(VIEWS)]
                ),
            )
        win.update_idletasks()
        owner = self.winfo_toplevel()
        x = max(
            0,
            min(
                owner.winfo_rootx() + (owner.winfo_width() - win.winfo_reqwidth()) // 2,
                win.winfo_screenwidth() - win.winfo_reqwidth() - 12,
            ),
        )
        y = max(
            0,
            min(
                owner.winfo_rooty() + (owner.winfo_height() - win.winfo_reqheight()) // 2,
                win.winfo_screenheight() - win.winfo_reqheight() - 64,
            ),
        )
        win.geometry(f"+{x}+{y}")
        win.grab_set()
        close.focus_set()

    def _update_large(self):
        win = self.large_preview
        key = self._preview_key()
        prefix = (
            "large/" if win.winfo_screenwidth() >= 940 and win.winfo_screenheight() >= 675 else ""
        )
        self.full_image = tk.PhotoImage(master=win, file=str(ART / (prefix + key + ".png")))
        self.large_image.configure(image=self.full_image)
        text = f"Monokai {self.skin} · {self.view.get()}"
        win.title(text)
        self.large_caption.configure(text=text)
        for name, button in self.large_buttons.items():
            button.state(["selected"] if name == self.view.get() else ["!selected"])

    def _close_large(self):
        if self.large_preview is not None:
            self.large_preview.destroy()
            self.large_preview = None
            self.full_image = None
            self.enlarge.focus_set()

    def _build_sleeves(self):
        panel = self._panel("Card sleeves")
        ttk.Label(panel, text="Card sleeves", style="StudioName.TLabel").grid(sticky="w")
        ttk.Label(
            panel, text="Choose the card back for each side of the duel.", style="StudioHint.TLabel"
        ).grid(sticky="w", pady=(1, 12))
        pair = ttk.Frame(panel, style="Studio.TFrame")
        pair.grid(sticky="ew")
        self.sleeve_images = []
        self.sleeve_labels = []
        for index, title in enumerate(("Your sleeve", "Opponent’s sleeve")):
            pair.columnconfigure(index, weight=1, uniform="sleeves")
            card = ttk.Frame(pair, style="Panel.TFrame", padding=(12, 18))
            card.grid(row=0, column=index, sticky="nsew", padx=(0, 4) if index == 0 else (4, 0))
            card.columnconfigure(1, weight=1)
            # Reserve the same portrait bounds for custom, original and missing artwork.
            preview = tk.Canvas(
                card, width=132, height=196, highlightthickness=0, background=SURFACE
            )
            preview.grid(row=0, column=0, sticky="w", padx=(0, 12))
            preview.create_rectangle(7, 5, 125, 191, outline=LINE, width=2)
            preview.create_polygon(66, 63, 89, 98, 66, 133, 43, 98, fill="", outline=LINE, width=2)
            image = preview.create_image(66, 98)
            self.sleeve_images.append((preview, image))
            options = ttk.Frame(card, style="Panel.TFrame")
            options.grid(row=0, column=1, sticky="w")
            ttk.Label(options, text=title, style="PanelName.TLabel").grid(sticky="w")
            label = ttk.Label(options, style="PanelHint.TLabel", wraplength=140)
            label.grid(sticky="w", pady=(1, 10))
            self.sleeve_labels.append(label)
            for name, value in (("Keep current", ""), *((name, name) for name in extras.SLEEVES)):
                button = ttk.Radiobutton(
                    options,
                    text=name,
                    variable=self.sleeves[index],
                    value=value,
                    style="Sleeve.TRadiobutton",
                    command=self._sleeves_changed,
                )
                button.grid(sticky="w")
                self.controls.append(button)

    def _identify_sleeves(self):
        """Decode a custom installed PNG only on opening the sleeves page."""
        if self.current_sleeves:
            return
        with ZipFile(extras.PACKS / "shared.zip") as archive:
            originals = {
                (archive.getinfo(file).file_size, archive.getinfo(file).CRC): name
                for name, file in extras.SLEEVES.items()
            }
        for i, basename in enumerate(("cover", "cover2")):
            self.current_sleeves[i] = ("Follows your sleeve" if i else "Game default", None)
            root = Path(self.game_path).resolve()
            path = extras.installed_sleeve(root, basename, self.active_skin)
            if path is None:
                continue
            self.current_sleeves[i] = ("Custom artwork", None)
            with contextlib.suppress(OSError, tk.TclError, struct.error):
                if (
                    not path.resolve().is_relative_to(root)
                    or path.stat().st_size > 16 * 1024 * 1024
                ):
                    continue
                with path.open("rb") as file:
                    head = file.read(24)
                    if not head.startswith(b"\x89PNG\r\n\x1a\n"):
                        continue
                    width, height = struct.unpack(">II", head[16:24])
                    if not (
                        0 < width <= 8192 and 0 < height <= 8192 and width * height <= 12_000_000
                    ):
                        continue
                    size = path.stat().st_size
                    if size in {length for length, _crc in originals}:
                        file.seek(0)
                        name = originals.get((size, zlib.crc32(file.read())))
                        if name:
                            self.current_sleeves[i] = (name, self._sleeve_image(name))
                            continue
                full = tk.PhotoImage(master=self, file=str(path))
                sample = max((width + 129) // 130, (height + 189) // 190)
                self.current_sleeves[i] = ("Custom artwork", full.subsample(max(1, sample)))

    def _sleeve_image(self, name):
        return self._image("sleeve-" + str(tuple(extras.SLEEVES).index(name)))

    def _sleeves_changed(self):
        self._identify_sleeves()
        for index, variable in enumerate(self.sleeves):
            installed, image = self.current_sleeves[index]
            if variable.get():
                image = self._sleeve_image(variable.get())
            elif index and installed == "Follows your sleeve":
                player = self.sleeves[0].get()
                image = self._sleeve_image(player) if player else self.current_sleeves[0][1]
            canvas, item = self.sleeve_images[index]
            canvas.itemconfigure(item, image=image or "")
            self.sleeve_labels[index].configure(
                text=installed if installed == "Follows your sleeve" else "Installed · " + installed
            )
        self._refresh_actions()

    def _build_effects(self):
        panel = self._panel("Duel effects")
        ttk.Label(panel, text="Duel sound effects", style="StudioName.TLabel").grid(sticky="w")
        ttk.Label(
            panel,
            text="Listen to the draw cue, then choose a sound pack.",
            style="StudioHint.TLabel",
        ).grid(sticky="w", pady=(1, 12))
        row = ttk.Frame(panel, style="Studio.TFrame")
        row.grid(sticky="ew")
        self.pack_buttons = {}
        self.listen_buttons = {}
        for i, (name, detail) in enumerate(
            (
                ("Thonk", "24 tactile cues · cards, counters and dice"),
                ("Synth", "10 electronic cues · draws, summons and attacks"),
            )
        ):
            row.columnconfigure(i, weight=1, uniform="packs")
            card = ttk.Frame(row, style="Panel.TFrame", padding=(13, 15))
            card.grid(row=0, column=i, sticky="ew", padx=(0, 4) if i == 0 else (4, 0))
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=name, style="PanelName.TLabel").grid(sticky="w")
            ttk.Label(card, text=detail, style="PanelHint.TLabel").grid(sticky="w", pady=(1, 12))
            ttk.Label(
                card,
                image=self._image(name.lower() + "-wave"),
                style="Panel.TLabel",
                anchor="center",
            ).grid(sticky="ew", pady=(4, 12))
            actions = ttk.Frame(card, style="Panel.TFrame")
            actions.grid(sticky="ew")
            actions.columnconfigure(0, weight=1)
            self.listen_buttons[name] = self._button(
                actions,
                "Play draw cue",
                lambda pack=name: self._audition(pack),
                style="Panel.TButton",
            )
            self.listen_buttons[name].grid(row=0, column=0, sticky="w")
            self.pack_buttons[name] = self._button(
                actions, "Select", lambda pack=name: self._select_pack(pack), style="Scene.TButton"
            )
            self.pack_buttons[name].grid(row=0, column=1)

    def _select_pack(self, name):
        self.pack.set(name)
        for key, button in self.pack_buttons.items():
            selected = key == name
            button.configure(text="Selected" if selected else "Select")
            button.state(["selected"] if selected else ["!selected"])
        self._refresh_actions()

    def _audition(self, name):
        try:
            if self.player is None:
                from music_player import PreviewPlayer

                self.player = PreviewPlayer()
            self.stop_preview()
            path = (
                ART / "thonk-draw.wav" if name == "Thonk" else extras.ASSETS / "sound" / "draw.wav"
            )
            self.player.play(path)
            self.listen_buttons[name].configure(text="Playing…")
            self.preview_after = self.after(220, self._poll_preview)
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self._message("Audio preview unavailable. " + str(exc), error=True)

    def _poll_preview(self):
        self.preview_after = None
        if self.player is not None and self.player.playing:
            self.preview_after = self.after(160, self._poll_preview)
        else:
            for button in self.listen_buttons.values():
                button.configure(text="Play draw cue")

    def stop_preview(self):
        if self.preview_after is not None:
            self.after_cancel(self.preview_after)
            self.preview_after = None
        if self.player is not None:
            with contextlib.suppress(OSError, RuntimeError):
                self.player.stop()
        for button in self.listen_buttons.values():
            if button.winfo_exists():
                button.configure(text="Play draw cue")

    def _build_music(self):
        panel = self._panel("Music")
        ttk.Label(panel, text="Background music", style="StudioName.TLabel").grid(sticky="w")
        ttk.Label(
            panel,
            text="Choose the music that plays while building a deck or duelling.",
            style="StudioHint.TLabel",
        ).grid(sticky="w", pady=(1, 12))
        from music_library import ART as cover_dir
        from music_library import catalog

        games = catalog()

        library = ttk.Frame(panel, style="Panel.TFrame", padding=(14, 12))
        library.grid(sticky="ew")
        library.columnconfigure(0, weight=1)
        heading = ttk.Frame(library, style="Panel.TFrame")
        heading.grid(sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="Game soundtracks", style="PanelName.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self._button(heading, "Browse soundtracks", self.open_music, style="Aqua.TButton").grid(
            row=0, column=1
        )
        covers = ttk.Frame(library, style="Panel.TFrame")
        covers.grid(sticky="ew", pady=(10, 0))
        for i, (key, title) in enumerate(
            (
                ("forbidden", "Forbidden Memories"),
                ("tf3", "Tag Force 3"),
                ("wc2011", "Over the Nexus"),
                ("legacy", "Legacy of the Duelist"),
            )
        ):
            covers.columnconfigure(i, weight=1, uniform="covers")
            image = tk.PhotoImage(master=self, file=str(cover_dir / "thumb" / (key + ".png")))
            self.images["album-" + key] = image
            tile = self._button(
                covers,
                f"{title}\n{games[key].year}",
                lambda game=key: self.open_music(game=game),
                image=image,
                compound="top",
                style="SkinTile.TButton",
            )
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 3, 0 if i == 3 else 3))
        pair = ttk.Frame(panel, style="Panel.TFrame", padding=(14, 12))
        pair.grid(sticky="ew", pady=(7, 0))
        pair.columnconfigure(0, weight=1)
        ttk.Label(pair, text="Bundled synth pair", style="PanelName.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            pair,
            text="Glass Archive · menus     /     False Shuffle · duels",
            style="PanelHint.TLabel",
        ).grid(row=1, column=0, sticky="w")
        self._button(pair, "Install synth pair", self._apply_selection, style="Panel.TButton").grid(
            row=0, column=1, rowspan=2, padx=(8, 0)
        )

    def _step_tab(self, offset):
        if not self.busy:
            self.tab.set(TABS[(TABS.index(self.tab.get()) + offset) % len(TABS)])
            self.show_tab()
            self.tabs[self.tab.get()].focus_set()
        return "break"

    def show_tab(self):
        self.stop_preview()
        tab = self.tab.get()
        for name, panel in self.panels.items():
            if name != tab:
                panel.grid_remove()
        self.panels[tab].grid(row=0, column=0, sticky="new")
        if tab == "Card sleeves":
            self._sleeves_changed()
        self._refresh_actions()
        self.on_layout()

    def open(self, game_path, config_path):
        self.game_path = game_path
        self.config_path = config_path
        self.active_skin = extras.current_skin(game_path)
        self.installed_highlights = {
            name: extras.turn_highlight_enabled(Path(game_path), name) for name in extras.SKINS
        }
        self.preview_highlights = self.installed_highlights.copy()
        self.current_sleeves.clear()
        for variable in self.sleeves:
            variable.set("")
        self.installed_label.set("Current skin · " + (self.active_skin or "EDOPro default"))
        name = self.active_skin.removeprefix("Monokai ")
        self.tab.set(TABS[0])
        self.view.set("Menu")
        self.select_skin(name if name in extras.SKINS else extras.SKINS[0])
        self._select_pack("Thonk")
        self.show_tab()
        # Keeping the actions in place prevents the window from jumping when a
        # user compares skins, sleeves and sound in the same visit.
        self.update_idletasks()
        self.body.rowconfigure(0, minsize=self.panels[TABS[0]].winfo_reqheight())
        self.on_layout()

    def _selection(self):
        match self.tab.get():
            case "Game skins":
                return {"skin": self.skin, "highlight_turn": self.turn_highlight.get()}
            case "Card sleeves":
                return {
                    key: v.get()
                    for key, v in zip(
                        ("player_sleeve", "opponent_sleeve"), self.sleeves, strict=True
                    )
                    if v.get()
                }
            case "Duel effects":
                return {"effects": self.pack.get()}
            case _:
                return {"synth_music": True}

    def _apply_selection(self):
        selection = self._selection()
        if not self.busy and selection:
            self.apply(selection)

    def _restore_current(self):
        if self.busy:
            return
        group = UNDO_GROUPS.get(self.tab.get())
        self.apply({"undo_group": group[0]} if group else {"restore": True})

    def _refresh_actions(self):
        tab = self.tab.get()
        button_text = {
            "Game skins": "Use " + self.skin,
            "Card sleeves": "Apply sleeves",
            "Duel effects": "Install " + self.pack.get() + " effects",
            "Music": "Install synth pair",
        }[tab]
        if tab == "Music":
            self.apply_button.grid_remove()
        else:
            self.apply_button.grid()
        if tab == "Game skins" and self.active_skin == "Monokai " + self.skin:
            if self.turn_highlight.get() != self.installed_highlights.get(self.skin):
                button_text = (
                    "Apply turn highlight"
                    if self.turn_highlight.get()
                    else "Restore original turn colours"
                )
            else:
                button_text = "Reinstall " + self.skin
        self.apply_button.configure(text=button_text)
        self.apply_button.state(
            ["disabled"] if self.busy or not self._selection() else ["!disabled"]
        )
        group = UNDO_GROUPS.get(tab)
        if group:
            show_undo = bool(self.game_path) and extras.can_undo(
                self.game_path, self.config_path, group[0]
            )
            undo_label = group[1]
        else:
            show_undo = bool(self.game_path) and extras.can_restore(
                self.game_path, self.config_path
            )
            undo_label = "Restore previous skin"
        if show_undo:
            self.restore_button.configure(text=undo_label)
            self.restore_button.grid()
        else:
            self.restore_button.grid_remove()
        if not self.busy:
            self._message("Close EDOPro before applying changes.")

    def _message(self, message, *, error=False, success=False):
        self.status.set(message)
        self.status_label.configure(
            style="StudioError.TLabel"
            if error
            else "StudioGood.TLabel"
            if success
            else "StudioHint.TLabel"
        )

    def set_busy(self, message):
        self.busy = True
        self.stop_preview()
        for control in self.controls:
            control.state(["disabled"])
        self._message(message)

    def finish(self, success, message):
        self.busy = False
        for control in self.controls:
            control.state(["!disabled"])
        if success:
            self.active_skin = extras.current_skin(self.game_path)
            self.installed_highlights[self.skin] = extras.turn_highlight_enabled(
                Path(self.game_path), self.skin
            )
            self.installed_label.set("Current skin · " + (self.active_skin or "EDOPro default"))
            if self.tab.get() == "Card sleeves":
                self.current_sleeves.clear()
                for variable in self.sleeves:
                    variable.set("")
                self._sleeves_changed()
        self._refresh_actions()
        self._message(message, error=not success, success=success)
        self.on_layout()
